import json


def load_config(path, defaults={}):
    data = json.load(open(path))
    for k in defaults:
        if k not in data:
            data[k] = defaults[k]
    return data
