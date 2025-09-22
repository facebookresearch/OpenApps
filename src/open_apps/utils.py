"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from omegaconf import DictConfig, OmegaConf


def _merge_dicts(base_dict, plus_dict, base_key, plus_key):
    merged_dict = dict(base_dict)
    for k, v in plus_dict.items():
        if k in merged_dict:
            if isinstance(merged_dict[k], list) and isinstance(v, list):
                merged_dict[k] = merged_dict[k] + v
                print(f"Merged {base_key}.{k}: {len(merged_dict[k]) - len(v)} + {len(v)} items")
            else:
                merged_dict[k] = v
                print(f"Overwrote {base_key}.{k} with {plus_key}.{k}")
        else:
            merged_dict[k] = v
            print(f"Added new key {base_key}.{k} from {plus_key}")
    return merged_dict

def _merge_lists(base_list, plus_list, base_key, plus_key):
    merged_list = list(base_list) + list(plus_list)
    print(f"Merged {base_key} with {plus_key}: {len(base_list)} + {len(plus_list)} items")
    return merged_list

def _merge_omegaconf_lists(base_value, plus_value, base_key, plus_key):
    merged_list = OmegaConf.to_container(base_value) + OmegaConf.to_container(plus_value)
    print(f"Merged {base_key} with {plus_key}: {len(base_value)} + {len(plus_value)} items")
    return merged_list

def _recursive_merge_plus_keys(config_dict):
    """
    Recursively merge keys that have a '+' version with their base keys.
    For example, merge 'chat_history' and '+chat_history' dictionaries,
    or merge 'saved_places' and '+saved_places' lists.
    """
    if not isinstance(config_dict, (dict, DictConfig)):
        return config_dict

    # First, recursively process nested dictionaries
    for key, value in list(config_dict.items()):
        if isinstance(value, (dict, DictConfig)):
            _recursive_merge_plus_keys(value)

    # Find all keys that start with '+'
    plus_keys = [key for key in config_dict.keys() if key.startswith('+')]

    for plus_key in plus_keys:
        base_key = plus_key[1:]
        if base_key in config_dict:
            base_value = config_dict[base_key]
            plus_value = config_dict[plus_key]

            if isinstance(base_value, (dict, DictConfig)) and isinstance(plus_value, (dict, DictConfig)):
                base_dict = OmegaConf.to_container(base_value) if isinstance(base_value, DictConfig) else base_value
                plus_dict = OmegaConf.to_container(plus_value) if isinstance(plus_value, DictConfig) else plus_value
                config_dict[base_key] = _merge_dicts(base_dict, plus_dict, base_key, plus_key)
                config_dict[plus_key] = None
                print(f"Merged dictionary {base_key} with {plus_key}")
            elif isinstance(base_value, list) and isinstance(plus_value, list):
                config_dict[base_key] = _merge_lists(base_value, plus_value, base_key, plus_key)
                config_dict[plus_key] = None
            elif OmegaConf.is_list(base_value) and OmegaConf.is_list(plus_value):
                config_dict[base_key] = _merge_omegaconf_lists(base_value, plus_value, base_key, plus_key)
                config_dict[plus_key] = None
            else:
                print(f"Warning: Cannot merge {base_key} and {plus_key} - incompatible types")
                print(f"  {base_key} type: {type(base_value)}")
                print(f"  {plus_key} type: {type(plus_value)}")
        else:
            print(f"Warning: Found {plus_key} but no corresponding {base_key} to merge with")
    return config_dict

def merge_plus_keys(config_dict):
    """
    Public API for recursively merging plus keys in a config dict.
    """
    return _recursive_merge_plus_keys(config_dict)