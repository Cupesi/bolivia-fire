from __future__ import annotations

import gc
import shutil
import time
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import torch
from numpy.typing import NDArray
from torch import nn
from torch.nn import functional as F

from bolivia_fire.comparison.data import Split, load_split
from bolivia_fire.comparison.runtime import Resources, Study, average_precision, log, now
from bolivia_fire.evaluation.artifacts import atomic_json

torch.set_num_threads(4)
torch.backends.cudnn.benchmark = False
torch.backends.cudnn.deterministic = True
torch.use_deterministic_algorithms(True)


class Grid:
    def __init__(self, study: Study, split: Split) -> None:
        self.split = split
        cells = pq.read_table(
            study.paths.datasets / "processed/cells.parquet", columns=["cell_id", "row", "col"]
        )
        cell_index = pd.Index(cells["cell_id"].to_numpy())
        rows, cols = cells["row"].to_numpy(), cells["col"].to_numpy()
        self.height, self.width = int(rows.max() - rows.min() + 1), int(cols.max() - cols.min() + 1)
        rows, cols = rows - rows.min(), cols - cols.min()
        dates, inverse = np.unique(split.issue, return_inverse=True)
        date_index = pd.Index(dates)
        positions = cell_index.get_indexer(split.column("cell_id"))
        if (positions < 0).any():
            raise ValueError("Comparison grid contains unknown cells")
        self.flat_index = (
            inverse * self.height * self.width + rows[positions] * self.width + cols[positions]
        ).astype(np.int64)
        if len(np.unique(self.flat_index)) != len(split.y):
            raise ValueError("Duplicate cell-weeks in comparison grid")
        self.n_weeks = len(dates)
        stem = study.cache / "data" / f"{split.name}_grid"
        x_path = stem.with_name(stem.name + "_x.npy")
        y_path = stem.with_name(stem.name + "_y.npy")
        if not x_path.exists():
            shape = (self.n_weeks, len(study.features), self.height, self.width)
            needed = int(np.prod(shape)) * 4
            if (
                shutil.disk_usage(study.paths.root).free - needed
                < study.config["minimum_free_gib"] * 1024**3
            ):
                raise ValueError("Grid allocation would exhaust the free disk reserve")
            grid = np.lib.format.open_memmap(x_path, mode="w+", dtype=np.float32, shape=shape)
            grid[:] = 0
            transform = joblib.load(study.models / "train_transform.joblib")
            for path in sorted(
                (study.paths.datasets / "processed/panel").glob("year=*/part.parquet")
            ):
                dataset = ds.dataset(path, format="parquet")
                condition = ds.field("partition") == split.name
                if not dataset.count_rows(filter=condition):
                    continue
                for batch in dataset.to_batches(
                    columns=["cell_id", "issue_date", *study.features],
                    filter=condition,
                    batch_size=100_000,
                ):
                    table = pa.Table.from_batches([batch])
                    week = date_index.get_indexer(table["issue_date"].to_numpy())
                    included = week >= 0
                    table, week = table.filter(pa.array(included)), week[included]
                    if not len(table):
                        continue
                    cell = cell_index.get_indexer(table["cell_id"].to_numpy())
                    if (cell < 0).any():
                        raise ValueError("Unknown spatial context cell")
                    values = np.column_stack(
                        [table[name].to_numpy(zero_copy_only=False) for name in study.features]
                    ).astype(np.float32)
                    values = transform.transform(values)
                    if not np.isfinite(values).all():
                        raise ValueError("Nonfinite normalized CNN input")
                    grid[week, :, rows[cell], cols[cell]] = values
                log(f"Built {split.name} input maps from {path.parent.name}; feature columns only")
            grid.flush()
            del grid
        if not y_path.exists():
            targets = np.lib.format.open_memmap(
                y_path,
                mode="w+",
                dtype=np.float32,
                shape=(self.n_weeks, self.height, self.width),
            )
            targets[:] = -1
            targets.reshape(-1)[self.flat_index] = split.y
            targets.flush()
            del targets
        self.x, self.y = np.load(x_path, mmap_mode="r"), np.load(y_path, mmap_mode="r")
        if np.count_nonzero(self.y >= 0) != len(split.y) or not np.array_equal(
            self.y.reshape(-1)[self.flat_index],
            split.y,
        ):
            raise ValueError("CNN grid labels do not match eligible panel rows")
        atomic_json(
            stem.with_name(stem.name + "_audit.json"),
            {
                "shape": list(self.x.shape),
                "loss_rows": len(split.y),
                "label_alignment_verified": True,
                "duplicate_cell_weeks": 0,
                "dates": np.datetime_as_string(dates, unit="D").tolist(),
                "label_policy": (
                    "Only eligible non-held-out labels; other targets -1 and loss masked"
                ),
                "input_policy": (
                    "Same-issue strict feature context, including held-out locations; "
                    "no context labels"
                ),
            },
        )


class SpatialCNN(nn.Module):
    def __init__(self, config: dict[str, Any], prevalence: float, channels: int = 21) -> None:
        super().__init__()
        width = config["width"]
        self.stem = nn.Sequential(nn.Conv2d(channels, width, 1), nn.GELU())
        layers = []
        for _ in range(config["depth"]):
            layers.extend(
                [
                    nn.Conv2d(width, width, 3, padding=1),
                    nn.GELU(),
                    nn.Dropout2d(config["dropout"]),
                ]
            )
        self.spatial = nn.Sequential(*layers)
        self.head, self.skip = nn.Conv2d(width, 1, 1), nn.Conv2d(channels, 1, 1, bias=False)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.skip.weight)
        nn.init.constant_(self.head.bias, float(np.log(prevalence / (1 - prevalence))))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return (self.head(self.spatial(self.stem(x))) + self.skip(x)).squeeze(1)


def device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def predict_grid(model: SpatialCNN, grid: Grid) -> NDArray:
    model.eval()
    output = np.empty((grid.n_weeks, grid.height, grid.width), dtype=np.float32)
    with torch.inference_mode():
        for start in range(0, grid.n_weeks, 4):
            x = torch.from_numpy(np.array(grid.x[start : start + 4], copy=True)).to(device())
            output[start : start + len(x)] = torch.sigmoid(model(x)).cpu().numpy()
    return output.reshape(-1)[grid.flat_index]


def predict_cnn(study: Study, split: Split) -> NDArray:
    checkpoint = torch.load(study.models / "spatial_cnn.pt", map_location="cpu", weights_only=True)
    model = SpatialCNN(checkpoint["config"], checkpoint["prevalence"], len(study.features))
    model.load_state_dict(checkpoint["state_dict"])
    return predict_grid(model.to(device()), Grid(study, split))


def train_cnn(study: Study) -> dict[str, Any]:
    study.require_unfrozen()
    settings = study.config["spatial_cnn"]
    with Resources(study, "spatial_cnn") as monitor:
        train, validation = load_split(study, "train"), load_split(study, "val_select")
        train_grid, validation_grid = Grid(study, train), Grid(study, validation)
        prevalence = float(train.y.mean())
        trials, best_ap = [], -1.0
        tick = time.perf_counter()
        for index, config in enumerate(settings["configs"]):
            started = time.perf_counter()
            torch.manual_seed(study.config["seed"])
            torch.cuda.manual_seed_all(study.config["seed"])
            rng = np.random.default_rng(study.config["seed"])
            model = SpatialCNN(config, prevalence, len(study.features)).to(device())
            optimizer = torch.optim.AdamW(
                model.parameters(),
                lr=config["learning_rate"],
                weight_decay=config["weight_decay"],
            )
            history = []
            trial_best_ap, best_epoch = -1.0, 0
            best_state, best_prediction = None, None
            for epoch in range(1, settings["max_epochs"] + 1):
                monitor.check()
                model.train()
                shuffled = rng.permutation(train_grid.n_weeks)
                total_loss, total_rows = 0.0, 0
                for start in range(0, len(shuffled), settings["batch_weeks"]):
                    positions = shuffled[start : start + settings["batch_weeks"]]
                    x = torch.from_numpy(np.array(train_grid.x[positions], copy=True)).to(device())
                    y = torch.from_numpy(np.array(train_grid.y[positions], copy=True)).to(device())
                    valid = y >= 0
                    optimizer.zero_grad(set_to_none=True)
                    losses = F.binary_cross_entropy_with_logits(
                        model(x), y.clamp_min(0), reduction="none"
                    )
                    loss = (losses * valid).sum() / valid.sum()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                    optimizer.step()
                    rows = int(valid.sum().item())
                    total_loss += float(loss.item()) * rows
                    total_rows += rows
                if total_rows != len(train.y):
                    raise ValueError(
                        "Each CNN epoch must use every eligible train row exactly once"
                    )
                p = predict_grid(model, validation_grid)
                ap = average_precision(validation.y, p)
                history.append(
                    {"epoch": epoch, "train_logloss": total_loss / total_rows, "val_select_ap": ap}
                )
                log(f"CNN {index + 1}/{len(settings['configs'])} epoch={epoch} AP={ap:.8f}")
                if ap > trial_best_ap:
                    trial_best_ap, best_epoch = ap, epoch
                    best_state = {
                        name: value.detach().cpu().clone()
                        for name, value in model.state_dict().items()
                    }
                    best_prediction = p.copy()
                study.save(
                    "spatial_cnn_progress",
                    {
                        "trial": index,
                        "config": config,
                        "epochs": history,
                        "resources": monitor.record(),
                    },
                )
                if epoch - best_epoch >= settings["early_stopping_epochs"]:
                    break
                if time.perf_counter() - tick > study.config["max_family_seconds"]:
                    break
            trial = {
                "index": index,
                "parameters": config,
                "best_epoch": best_epoch,
                "epochs_run": len(history),
                "val_select_ap": trial_best_ap,
                "runtime_seconds": time.perf_counter() - started,
                "history": history,
                "parameter_count": sum(value.numel() for value in model.parameters()),
            }
            trials.append(trial)
            if trial_best_ap > best_ap:
                best_ap = trial_best_ap
                torch.save(
                    {"config": config, "prevalence": prevalence, "state_dict": best_state},
                    study.models / "spatial_cnn.pt",
                )
                study.save_prediction("spatial_cnn", "val_select", best_prediction)
            study.save("spatial_cnn_trials", trials)
            del model, optimizer, best_state, best_prediction
            gc.collect()
            torch.cuda.empty_cache()
            if time.perf_counter() - tick > study.config["max_family_seconds"]:
                break
        result = {
            "family": "spatial_cnn",
            "selected_utc": now(),
            "selected_trial": max(trials, key=lambda item: item["val_select_ap"]),
            "completed_configurations": len(trials),
            "planned_configurations": len(settings["configs"]),
            "selection_source": "val_select exact AP only",
            "resources": monitor.close(),
            "cuda_peak_allocated_gib": torch.cuda.max_memory_allocated() / 1024**3
            if device() == "cuda"
            else 0,
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
            "device": device(),
            "torch_version": str(torch.__version__),
            "cuda_version": torch.version.cuda,
            "cudnn_version": torch.backends.cudnn.version(),
            "val_select_metrics": validation.score(study.prediction("spatial_cnn", "val_select")),
        }
        study.save("spatial_cnn_selected", result)
        return result
