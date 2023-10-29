from typing import Callable, Optional, Tuple

import pytest
import torch
import torch.nn.functional as F
from torch import nn

from latentis import transforms
from latentis.estimate.orthogonal import SVDEstimator
from latentis.space import LatentSpace
from latentis.translate.translator import LatentTranslator


def manual_svd_translation(A: torch.Tensor, B: torch.Tensor) -> torch.Tensor:
    # """Compute the translation vector that aligns A to B using SVD."""
    assert A.size(1) == B.size(1)
    u, s, vt = torch.svd((B.T @ A).T)
    R = u @ vt.T
    return R, s


class ManualLatentTranslation(nn.Module):
    def __init__(self, seed: int, centering: bool, std_correction: bool, l2_norm: bool, method: str) -> None:
        super().__init__()

        self.seed: int = seed
        self.centering: bool = centering
        self.std_correction: bool = std_correction
        self.l2_norm: bool = l2_norm
        self.method: str = method
        self.sigma_rank: Optional[float] = None

        self.translation_matrix: Optional[torch.Tensor]
        self.mean_encoding_anchors: Optional[torch.Tensor]
        self.mean_decoding_anchors: Optional[torch.Tensor]
        self.std_encoding_anchors: Optional[torch.Tensor]
        self.std_decoding_anchors: Optional[torch.Tensor]
        self.encoding_norm: Optional[torch.Tensor]
        self.decoding_norm: Optional[torch.Tensor]

    @torch.no_grad()
    def fit(self, encoding_anchors: torch.Tensor, decoding_anchors: torch.Tensor) -> None:
        if self.method == "absolute":
            return
        # First normalization: 0 centering
        if self.centering:
            mean_encoding_anchors: torch.Tensor = encoding_anchors.mean(dim=(0,))
            mean_decoding_anchors: torch.Tensor = decoding_anchors.mean(dim=(0,))
        else:
            mean_encoding_anchors: torch.Tensor = torch.as_tensor(0)
            mean_decoding_anchors: torch.Tensor = torch.as_tensor(0)

        if self.std_correction:
            std_encoding_anchors: torch.Tensor = encoding_anchors.std(dim=(0,))
            std_decoding_anchors: torch.Tensor = decoding_anchors.std(dim=(0,))
        else:
            std_encoding_anchors: torch.Tensor = torch.as_tensor(1)
            std_decoding_anchors: torch.Tensor = torch.as_tensor(1)

        self.encoding_dim: int = encoding_anchors.size(1)
        self.decoding_dim: int = decoding_anchors.size(1)

        self.register_buffer("mean_encoding_anchors", mean_encoding_anchors)
        self.register_buffer("mean_decoding_anchors", mean_decoding_anchors)
        self.register_buffer("std_encoding_anchors", std_encoding_anchors)
        self.register_buffer("std_decoding_anchors", std_decoding_anchors)

        encoding_anchors = (encoding_anchors - mean_encoding_anchors) / std_encoding_anchors
        decoding_anchors = (decoding_anchors - mean_decoding_anchors) / std_decoding_anchors

        self.register_buffer("encoding_norm", encoding_anchors.norm(p=2, dim=-1).mean())
        self.register_buffer("decoding_norm", decoding_anchors.norm(p=2, dim=-1).mean())

        # Second normalization: scaling
        if self.l2_norm:
            encoding_anchors = F.normalize(encoding_anchors, p=2, dim=-1)
            decoding_anchors = F.normalize(decoding_anchors, p=2, dim=-1)

        if self.method == "linear":
            with torch.enable_grad():
                translation = nn.Linear(
                    encoding_anchors.size(1), decoding_anchors.size(1), device=encoding_anchors.device
                )
                optimizer = torch.optim.Adam(translation.parameters(), lr=1e-3)

                for _ in range(300):
                    optimizer.zero_grad()
                    loss = F.mse_loss(translation(encoding_anchors), decoding_anchors)
                    loss.backward()
                    optimizer.step()
                self.translation = translation.cpu()
            return

        if self.method == "svd":
            # padding if necessary
            if encoding_anchors.size(1) < decoding_anchors.size(1):
                padded = torch.zeros_like(decoding_anchors)
                padded[:, : encoding_anchors.size(1)] = encoding_anchors
                encoding_anchors = padded
            elif encoding_anchors.size(1) > decoding_anchors.size(1):
                padded = torch.zeros_like(encoding_anchors)
                padded[:, : decoding_anchors.size(1)] = decoding_anchors
                decoding_anchors = padded

                self.encoding_anchors = encoding_anchors
                self.decoding_anchors = decoding_anchors

            translation_matrix, sigma = manual_svd_translation(A=encoding_anchors, B=decoding_anchors)
            self.sigma_rank = (~sigma.isclose(torch.zeros_like(sigma), atol=1e-1).bool()).sum().item()
        elif self.method == "lstsq":
            translation_matrix = torch.linalg.lstsq(encoding_anchors, decoding_anchors).solution
        elif self.method == "lstsq+ortho":
            translation_matrix = torch.linalg.lstsq(encoding_anchors, decoding_anchors).solution
            U, _, Vt = torch.svd(translation_matrix)
            translation_matrix = U @ Vt.T
        else:
            raise NotImplementedError

        translation_matrix = torch.as_tensor(
            translation_matrix, dtype=encoding_anchors.dtype, device=encoding_anchors.device
        )
        self.register_buffer("translation_matrix", translation_matrix)

        self.translation = lambda x: x @ self.translation_matrix

    def transform(self, X: torch.Tensor, compute_info: bool = True) -> torch.Tensor:
        if self.method == "absolute":
            return {"source": X, "target": X, "info": {}}

        encoding_x = (X - self.mean_encoding_anchors) / self.std_encoding_anchors

        if self.l2_norm:
            encoding_x = F.normalize(encoding_x, p=2, dim=-1)

        if self.method == "svd" and self.encoding_dim < self.decoding_dim:
            padded = torch.zeros(X.size(0), self.decoding_dim, device=X.device, dtype=X.dtype)
            padded[:, : self.encoding_dim] = encoding_x
            encoding_x = padded

        decoding_x = self.translation(encoding_x)

        decoding_x = decoding_x[:, : self.decoding_dim]

        # restore scale
        if self.l2_norm:
            decoding_x = decoding_x * self.decoding_norm

        # restore center
        decoding_x = (decoding_x * self.std_decoding_anchors) + self.mean_decoding_anchors

        info = {}
        if compute_info:
            pass

        return {"source": encoding_x, "target": decoding_x, "info": info}


@pytest.mark.parametrize(
    "eq_methods",
    [
        (
            lambda: ManualLatentTranslation(
                seed=0,
                centering=True,
                std_correction=True,
                l2_norm=False,
                method="svd",
            ),
            lambda: LatentTranslator(
                random_seed=0,
                estimator=SVDEstimator(),
                source_transforms=[transforms.Centering(), transforms.STDScaling()],
                target_transforms=[transforms.Centering(), transforms.STDScaling()],
            ),
        ),
        (
            lambda: ManualLatentTranslation(
                seed=0,
                centering=True,
                std_correction=True,
                l2_norm=False,
                method="svd",
            ),
            lambda: LatentTranslator(
                random_seed=0,
                estimator=SVDEstimator(),
                source_transforms=[transforms.StandardScaling()],
                target_transforms=[transforms.StandardScaling()],
            ),
        ),
        (
            lambda: ManualLatentTranslation(
                seed=0,
                centering=True,
                std_correction=False,
                l2_norm=True,
                method="svd",
            ),
            lambda: LatentTranslator(
                random_seed=0,
                estimator=SVDEstimator(),
                source_transforms=[transforms.Centering(), transforms.L2()],
                target_transforms=[transforms.Centering(), transforms.L2()],
            ),
        ),
        (
            lambda: ManualLatentTranslation(
                seed=0,
                centering=False,
                std_correction=False,
                l2_norm=True,
                method="svd",
            ),
            lambda: LatentTranslator(
                random_seed=0,
                estimator=SVDEstimator(),
                source_transforms=[transforms.L2()],
                target_transforms=[transforms.L2()],
            ),
        ),
        (
            lambda: ManualLatentTranslation(
                seed=0,
                centering=True,
                std_correction=False,
                l2_norm=False,
                method="svd",
            ),
            lambda: LatentTranslator(
                random_seed=0,
                estimator=SVDEstimator(),
                source_transforms=[transforms.Centering()],
                target_transforms=[transforms.Centering()],
            ),
        ),
    ],
)
def test_manual_translation(
    eq_methods: Tuple[Callable[[], ManualLatentTranslation], Callable[[], LatentTranslator]],
    parallel_spaces: Tuple[LatentSpace, LatentSpace],
):
    space1, space2 = parallel_spaces
    A = space1.vectors
    B = space2.vectors

    manual_translator, translator = eq_methods[0](), eq_methods[1]()

    manual_translator.fit(A, B)
    translator.fit(source_data=A, target_data=B)

    manual_output = manual_translator.transform(A)
    latentis_output = translator(A)

    assert torch.allclose(manual_output["target"], latentis_output["target"])
