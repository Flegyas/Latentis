from typing import Optional, Sequence

import datasets
from datasets import ClassLabel, DatasetDict

from latentis.data.dataset import DatasetView, Feature, FeatureMapping, HFDatasetView


class LoadHFDataset:
    def __init__(self, **load_params) -> None:
        self.load_params = load_params

    def __call__(self) -> DatasetDict:
        return datasets.load_dataset(**self.load_params)


def load_hf_dataset(**load_params) -> DatasetDict:
    return datasets.load_dataset(**load_params)


def subset(data: DatasetDict, perc: float, seed: int) -> DatasetDict:
    """Subset a dataset."""
    return DatasetDict(
        {
            split: data[split].shuffle(seed=seed).select(list(range(int(len(data[split]) * perc))))
            for split in data.keys()
        }
    )


class Subset:
    def __init__(self, perc: float, seed: int) -> None:
        self.perc = perc
        self.seed = seed

    def __call__(self, data: DatasetDict) -> DatasetDict:
        return subset(data, perc=self.perc, seed=self.seed)


def select_columns(data: DatasetDict, columns: Sequence[str]) -> DatasetDict:
    """Select columns from a dataset."""
    to_prune = {col for cols in data.column_names.values() for col in cols} - set(columns)
    return data.remove_columns(to_prune)


def store_to_disk(data: DatasetDict, path: str) -> DatasetDict:
    """Store a dataset to disk."""
    data.save_to_disk(path)
    return data


def add_id_column(data: DatasetDict, id_column: str) -> DatasetDict:
    """Add an id column to a dataset."""
    return data.map(
        lambda _, index: {id_column: index},
        with_indices=True,
    )


class HFMap:
    def __init__(self, input_columns: Sequence[str], fn):
        self.input_columns = input_columns
        self.fn = fn

    def __call__(self, data: DatasetDict) -> DatasetDict:
        data = data.map(
            self.fn,
            input_columns=self.input_columns,
            batched=True,
        )
        return data


class PruneColumns:
    def __init__(self, keep: Optional[Sequence[str]] = None, remove: Optional[Sequence[str]] = None) -> None:
        if keep is not None and remove is not None:
            raise ValueError("Cannot specify both `keep` and `remove`.")
        if keep is None and remove is None:
            raise ValueError("Must specify either `keep` or `remove`.")

        self.keep = keep
        self.remove = remove

    def __call__(self, data: DatasetDict) -> DatasetDict:
        if self.keep is not None:
            to_keep = self.keep
        else:
            to_keep = set(data.column_names) - set(self.remove)

        return select_columns(data, columns=to_keep)


class MapFeatures:
    def __init__(self, *feature_mappings: FeatureMapping) -> None:
        self.feature_mappings = feature_mappings

    def __call__(self, data: DatasetDict) -> DatasetDict:
        data = data.map(
            lambda *source_col_vals: {
                target_col: source_col_val
                for source_col_val, target_col in zip(
                    source_col_vals, [feature_mapping.target_col for feature_mapping in self.feature_mappings]
                )
            },
            batched=True,
            input_columns=[feature_mapping.source_col for feature_mapping in self.feature_mappings],
        )
        return data


class FeatureCast:
    def __init__(self, feature_name: str, feature) -> None:
        self.feature_name = feature_name
        self.feature = feature

    def __call__(self, data: DatasetDict) -> DatasetDict:
        return data.cast_column(data, self.feature_name, self.feature)


class ClassLabelCast:
    def __init__(self, column_name: str) -> None:
        self.column_name: str = column_name

    def __call__(self, data: DatasetDict) -> DatasetDict:
        class_label = ClassLabel(
            num_classes=len(set(data["train"][self.column_name])),
            names=list(set(data["train"][self.column_name])),
        )

        return data.cast_column(
            self.column_name,
            class_label,
        )


class ToView:
    def __init__(self, name: str, id_column: str, features: Sequence[Feature]):
        self.name = name
        self.id_column = id_column
        self.features = features

    def __call__(self, data: DatasetDict) -> DatasetView:
        if self.id_column not in data.column_names:
            data = data.map(
                lambda _, index: {self.id_column: index},
                with_indices=True,
            )

        return HFDatasetView(
            name=self.name,
            hf_dataset=data,
            id_column=self.id_column,
            features=self.features,
        )


def imdb_process(data: DatasetDict, seed: int = 42):
    del data["unsupervised"]
    fit_data = data["train"].train_test_split(test_size=0.1, seed=seed)
    data["train"] = fit_data["train"]
    data["val"] = fit_data["test"]

    data = data.cast_column(
        "label",
        ClassLabel(
            num_classes=len(set(data["train"]["label"])),
            names=list(set(data["train"]["label"])),
        ),
    )

    return data