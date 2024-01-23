import functools
import itertools
import logging
from typing import Sequence

import torch
from torch import nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from latentis.data import DATA_DIR
from latentis.data.dataset import Feature
from latentis.data.processor import LatentisDataset
from latentis.data.text_encoding import HFPooler, cls_pool
from latentis.data.utils import default_collate
from latentis.modules import LatentisModule, TextHFEncoder
from latentis.space import LatentSpace

pylogger = logging.getLogger(__name__)

DEFAULT_ENCODERS = {
    "text": [
        "bert-base-cased",
        "bert-base-uncased",
        "google/electra-base-discriminator",
        "roberta-base",
        "albert-base-v2",
        "xlm-roberta-base",
        "openai/clip-vit-base-patch32",
    ],
    "image": [
        "rexnet_100",
        "vit_base_patch16_224",
        "vit_base_patch16_384",
        "vit_base_resnet50_384",
        "vit_small_patch16_224",
        "openai/clip-vit-base-patch32",
        "efficientnet_b1_pruned",
        "regnety_002",
        "cspresnext50",
        "cspdarknet53",
    ],
}


# encoding_module = encoder.vision_model if encoder_name.startswith("openai/clip") else encoder
# for batch in tqdm(loader, desc=f"Embedding samples"):
#     embeddings.extend(encoding_module(batch["image"].to(DEVICE)).cpu().tolist())

# return embeddings


def encode_feature(
    dataset: LatentisDataset,
    feature: str,
    model: LatentisModule,
    collate_fn: callable,
    encoding_batch_size: int,
    store_every: int,
    num_workers: int,
    device: torch.device,
    save_source_model: bool = False,
    poolers: Sequence[nn.Module] = None,
):
    """Encode the dataset using the specified encoders.

    Args:
        dataset (LatentisDataset): Dataset to encode
        feature (str): Name of the feature to encode
        model (LatentisModule): Model to use for encoding
        collate_fn (callable): Collate function to use for the dataset
        encoding_batch_size (int): Batch size for encoding
        store_every (int): Store encodings every `store_every` batches
        num_workers (int): Number of workers to use for encoding
        device (torch.device): Device to use for encoding
        save_source_model (bool, optional): Save the source model. Defaults to False.
        poolers (Sequence[nn.Module], optional): Poolers to use for encoding. Defaults to None.
    """
    feature: Feature = dataset.get_feature(feature)

    pylogger.info(f"Encoding {feature} for dataset {dataset._name}")

    for split, split_data in dataset.hf_dataset.items():
        loader = DataLoader(
            split_data,
            batch_size=encoding_batch_size,
            pin_memory=device != torch.device("cpu"),
            shuffle=False,
            num_workers=num_workers,
            collate_fn=functools.partial(collate_fn, model=model, feature=feature.col_name),
        )

        for batch in tqdm(loader, desc=f"Encoding `{split}` samples for feature {feature.col_name} using {model.key}"):
            raw_encoding = model.encode(batch)

            if len(poolers) == 0:
                assert isinstance(raw_encoding, torch.Tensor)

                dataset.add_encoding(
                    item=LatentSpace(
                        vector_source=(raw_encoding, batch[dataset._id_column]),
                        info={
                            "model": model.key,
                            "feature": feature.col_name,
                            "split": split,
                            "dataset": dataset.name,
                        },
                    ),
                    save_source_model=save_source_model,
                )
            else:
                for pooler in poolers:
                    encoding2pooler_properties = pooler(**raw_encoding)

                    for encoding, pooler_properties in encoding2pooler_properties:
                        dataset.add_encoding(
                            item=LatentSpace(
                                vector_source=(encoding, batch[dataset._id_column]),
                                info={
                                    "model": model.key,
                                    "feature": feature.col_name,
                                    "split": split,
                                    "dataset": dataset.name,
                                    **pooler_properties,
                                },
                            ),
                            save_source_model=save_source_model,
                        )

        # model.cpu()


if __name__ == "__main__":
    for dataset, hf_encoder in itertools.product(
        ["trec"], ["bert-base-cased", "bert-base-uncased", "bert-large-cased"]
    ):
        dataset = LatentisDataset.load_from_disk(DATA_DIR / dataset)

        encode_feature(
            dataset=dataset,
            feature="text",
            model=TextHFEncoder(hf_encoder),
            collate_fn=default_collate,
            encoding_batch_size=256,
            store_every=10,
            num_workers=0,
            save_source_model=False,
            poolers=[
                HFPooler(layers=[12], pooling_fn=cls_pool),
                # HFPooler(layers=list(range(13)), pooling_fn=sum_pool),
            ],
            device=torch.device("cpu"),
        )
