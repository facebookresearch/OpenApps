"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import hydra
import os
from omegaconf import DictConfig, OmegaConf


def load_config(config_path: str | None = None) -> DictConfig:
    if config_path is None:
        config_path = os.environ.get("EXPERIMENT_CONFIG_PATH", None)
        if config_path is None:
            print("No EXPERIMENT_CONFIG_PATH env var set, loading a default config, ")
            return create_default_config()
    print("loading config from ", config_path)
    with open(config_path) as fp:
        config = OmegaConf.load(fp)
    return config


def create_default_config() -> DictConfig:
    try:
        with hydra.initialize(version_base=None, config_path="../configs/"):
            config = hydra.compose(config_name="defaults.yaml")
    except ValueError:
        print("Hydra already initiliazed loading conifg")
        config = hydra.compose(config_name="defaults.yaml")

    return config
