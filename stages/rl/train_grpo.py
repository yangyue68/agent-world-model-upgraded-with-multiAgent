# -*- coding: utf-8 -*-
"""GRPO 训练命令行入口。"""

import sys
from pathlib import Path

_MA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_MA_ROOT) not in sys.path:
    sys.path.insert(0, str(_MA_ROOT))

from lib.interrupt import run_cli
from stages.rl.config import Config
from stages.rl.training_loop import train


def main():
    from simpleArgParser import parse_args

    train(parse_args(Config))


if __name__ == "__main__":
    run_cli(main)
