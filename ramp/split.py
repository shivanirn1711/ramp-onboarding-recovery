"""Deterministic tune/test split of the coded rows, for the honest accuracy test.

Only rows that have BOTH a reviewer code and a reviewer message can test the
keyword rules (the code is the answer, the message is the input).

Split is stratified by code so every code with 2+ rows appears on both
sides. When a code has an odd number of rows, the spare row alternates
between tune and test (so the halves end up roughly equal). The seed is fixed
in config.py and was set before any keyword rule was written.
"""
import random

import pandas as pd

from . import config


def split_coded(apps: pd.DataFrame, seed: int = config.SPLIT_SEED) -> pd.DataFrame:
    """Return a DataFrame [application_id, rejection_code, split] with split in {tune, test}."""
    coded = apps[apps["rejection_code"].notna() & apps["rejection_message"].notna()]
    rng = random.Random(seed)
    spare_to_tune = True
    rows = []
    for code in sorted(coded["rejection_code"].unique()):
        ids = sorted(coded.loc[coded["rejection_code"] == code, "application_id"])
        rng.shuffle(ids)
        n_tune = len(ids) // 2
        if len(ids) % 2 == 1:
            if spare_to_tune:
                n_tune += 1
            spare_to_tune = not spare_to_tune
        for i, app_id in enumerate(ids):
            rows.append({"application_id": app_id, "rejection_code": code,
                         "split": "tune" if i < n_tune else "test"})
    return pd.DataFrame(rows).sort_values("application_id").reset_index(drop=True)
