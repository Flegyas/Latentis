import logging
from abc import abstractmethod
from typing import Any, Mapping, Sequence

from datasets import ClassLabel, Dataset, DatasetDict, load_dataset
from torchvision.transforms import Compose, ToTensor

from latentis.data.dataset import (
    Feature,
    FeatureDataType,
    FeatureMapping,
    FeatureProperty,
    LatentisDataset,
    Task,
    TaskType,
)

pylogger = logging.getLogger(__name__)


def map_features(dataset: Dataset, *feature_mappings: FeatureMapping):
    dataset = dataset.map(
        lambda *source_col_vals: {
            target_col: source_col_val
            for source_col_val, target_col in zip(
                source_col_vals, [feature_mapping.target_col for feature_mapping in feature_mappings]
            )
        },
        batched=True,
        input_columns=[feature_mapping.source_col for feature_mapping in feature_mappings],
    )

    # for feature_mapping in feature_mappings:
    #     dataset = dataset.cast_column(
    #         feature_mapping.target_col,
    #         feature=dataset.features[feature_mapping.source_col].dtype,
    #     )

    return dataset


_RANDOM_SEED: int = 42


class DataProcessor:
    def __init__(
        self,
        dataset_name: str,
        load_dataset_params: Mapping[str, Any],
        features: Sequence[Feature],
        tasks: Sequence[Task],
        properties={},
        id_column: str = "sample_id",
    ):
        self.dataset_name: str = dataset_name
        self.load_dataset_params = load_dataset_params
        self.features = features
        self.tasks = tasks
        self.properties = properties
        self.id_column = id_column

    @abstractmethod
    def _process(self, dataset: DatasetDict) -> DatasetDict:
        raise NotImplementedError

    def process(self, perc: float = 1) -> LatentisDataset:
        dataset: DatasetDict = load_dataset(**self.load_dataset_params)

        # Select a random subset, if needed
        if perc != 1:
            dataset = DatasetDict(
                {
                    split: dataset[split]
                    .shuffle(seed=_RANDOM_SEED)
                    .select(list(range(int(len(dataset[split]) * perc))))
                    for split in dataset.keys()
                }
            )
        start_columns = {col for cols in dataset.column_names.values() for col in cols}
        core_columns = set([feature.col_name for feature in self.features] + [task.col_name for task in self.tasks])

        dataset: DatasetDict = self._process(dataset=dataset)

        dataset = dataset.remove_columns([col for col in start_columns if col not in core_columns])

        dataset = dataset.map(
            lambda _, index: {self.id_column: index},
            with_indices=True,
        )

        processed_dataset = LatentisDataset(
            dataset=dataset,
            name=self.dataset_name,
            id_column=self.id_column,
            features=self.features,
            tasks=self.tasks,
            perc=perc,
            properties=self.properties,
        )

        return processed_dataset


class DBPedia14(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="dbpedia_14",
            load_dataset_params=dict(path="dbpedia_14"),
            features=[
                Feature(col_name="x", data_type=FeatureDataType.TEXT, properties={FeatureProperty.LANGUAGE: "en"})
            ],
            tasks=[
                Task(col_name="y", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> LatentisDataset:
        dataset = dataset.map(
            lambda title, content: {
                "x": [title + ". " + content.strip('"').strip() for title, content in zip(title, content)]
            },
            input_columns=["title", "content"],
            batched=True,
        )
        dataset = map_features(dataset, FeatureMapping(source_col="label", target_col="y"))

        return dataset


class TREC(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="trec",
            load_dataset_params=dict(path="trec"),
            features=[
                Feature(col_name="text", data_type=FeatureDataType.TEXT, properties={FeatureProperty.LANGUAGE: "en"})
            ],
            tasks=[
                Task(col_name="coarse_label", task_type=TaskType.CLASSIFICATION),
                Task(col_name="fine_label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        return dataset


class AGNews(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="ag_news",
            load_dataset_params=dict(path="ag_news"),
            features=[
                Feature(col_name="text", data_type=FeatureDataType.TEXT, properties={FeatureProperty.LANGUAGE: "en"})
            ],
            tasks=[
                Task(col_name="label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        dataset = dataset.cast_column(
            "label",
            ClassLabel(
                num_classes=len(set(dataset["train"]["label"])),
                names=list(set(dataset["train"]["label"])),
            ),
        )

        return dataset


class IMDB(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="imdb",
            load_dataset_params=dict(path="imdb"),
            features=[
                Feature(col_name="text", data_type=FeatureDataType.TEXT, properties={FeatureProperty.LANGUAGE: "en"})
            ],
            tasks=[
                Task(col_name="label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        del dataset["unsupervised"]
        fit_data = dataset["train"].train_test_split(test_size=0.1, seed=_RANDOM_SEED)
        dataset["train"] = fit_data["train"]
        dataset["val"] = fit_data["test"]

        dataset = dataset.cast_column(
            "label",
            ClassLabel(
                num_classes=len(set(dataset["train"]["label"])),
                names=list(set(dataset["train"]["label"])),
            ),
        )

        return dataset


class MNIST(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="mnist",
            load_dataset_params=dict(path="mnist"),
            features=[
                Feature(col_name="image", data_type=FeatureDataType.IMAGE),
            ],
            tasks=[
                Task(col_name="label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        transforms = Compose(
            [
                ToTensor(),
            ]
        )

        dataset = dataset.map(
            lambda images: {"image": [transforms(image.convert("RGB")) for image in images]},
            input_columns=["image"],
            batched=True,
        )
        dataset = dataset.with_format("torch", columns=["image"])

        dataset = dataset.cast_column(
            "label",
            ClassLabel(
                num_classes=len(set(dataset["train"]["label"])),
                names=list(set(dataset["train"]["label"])),
            ),
        )

        return dataset


class CIFAR10(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="cifar10",
            load_dataset_params=dict(path="cifar10"),
            features=[
                Feature(col_name="image", data_type=FeatureDataType.IMAGE),
            ],
            tasks=[
                Task(col_name="label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        transforms = Compose(
            [
                ToTensor(),
            ]
        )

        dataset = dataset.map(
            lambda images: {"image": [transforms(image.convert("RGB")) for image in images]},
            input_columns=["img"],
            batched=True,
        )
        dataset = dataset.with_format("torch", columns=["image"])

        dataset = dataset.cast_column(
            "label",
            ClassLabel(
                num_classes=len(set(dataset["train"]["label"])),
                names=list(set(dataset["train"]["label"])),
            ),
        )

        return dataset


class CIFAR100(DataProcessor):
    def __init__(self):
        super().__init__(
            dataset_name="cifar100",
            load_dataset_params=dict(path="cifar100"),
            features=[
                Feature(col_name="image", data_type=FeatureDataType.IMAGE),
            ],
            tasks=[
                Task(col_name="fine_label", task_type=TaskType.CLASSIFICATION),
                Task(col_name="coarse_label", task_type=TaskType.CLASSIFICATION),
            ],
        )

    def _process(self, dataset: DatasetDict) -> DatasetDict:
        transforms = Compose(
            [
                ToTensor(),
            ]
        )

        dataset = dataset.map(
            lambda images: {"image": [transforms(image.convert("RGB")) for image in images]},
            input_columns=["img"],
            batched=True,
        )
        dataset = dataset.with_format("torch", columns=["image"])

        for label in ("coarse_label", "fine_label"):
            dataset = dataset.cast_column(
                label,
                ClassLabel(
                    num_classes=len(set(dataset["train"][label])),
                    names=list(set(dataset["train"][label])),
                ),
            )

        return dataset
