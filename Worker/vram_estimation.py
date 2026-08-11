import argparse
import json
import os
import runpy
import sys
import time
import types

import torch

# Configuration from environment
PATIENCE = int(os.environ.get("VRAM_PROBE_PATIENCE", "2"))
MIN_STEPS = int(os.environ.get("VRAM_PROBE_MIN_STEPS", "2"))
MAX_STEPS = int(os.environ.get("VRAM_PROBE_MAX_STEPS", "20"))
EARLY_STOP = os.environ.get("VRAM_PROBE_NO_EARLY_STOP", "0") != "1"
TIMING_WARMUP_STEPS = int(os.environ.get("VRAM_PROBE_TIMING_WARMUP_STEPS", "1"))


class ProbeDone(Exception):
    pass


def estimate(target, target_args):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available in this container")

    step_times = []
    state = {"stable_streak": 0, "last_step_time": None, "last_peak": None, "steps": 0}
    original_init = torch.optim.Optimizer.__init__

    def optimizer_init(optimizer, *args, **kwargs):
        original_init(optimizer, *args, **kwargs)
        original_step = optimizer.step

        def step_wrapper(_self, *step_args, **step_kwargs):
            result = original_step(*step_args, **step_kwargs)
            torch.cuda.synchronize()
            now = time.perf_counter()
            peak_reserved_memory = torch.cuda.max_memory_reserved() / 1e9
            
            if state["last_step_time"] is not None:
                step_times.append(now - state["last_step_time"])
            state["last_step_time"] = now
            state["steps"] += 1

            if state["last_peak"] is not None:
                state["stable_streak"] = 0 if peak_reserved_memory > state["last_peak"] else state["stable_streak"] + 1
            state["last_peak"] = peak_reserved_memory

            if EARLY_STOP and (state["steps"] >= MAX_STEPS or (
                state["steps"] >= MIN_STEPS and state["stable_streak"] >= PATIENCE
            )):
                raise ProbeDone()
            return result

        optimizer.step = types.MethodType(step_wrapper, optimizer)

    torch.optim.Optimizer.__init__ = optimizer_init
    torch.cuda.reset_peak_memory_stats()
    sys.argv = [target, *target_args]
    sys.path.insert(0, os.path.dirname(target))

    try:
        runpy.run_path(target, run_name="__main__")
    except (ProbeDone, SystemExit):
        pass
    finally:
        torch.optim.Optimizer.__init__ = original_init

    durations = step_times[TIMING_WARMUP_STEPS:] or step_times
    step_wall_time = sum(durations) / len(durations) if durations else None
    
    return {
        "peak_reserved_memory": round(torch.cuda.max_memory_reserved() / 1e9, 3),
        "step_wall_time": round(step_wall_time, 4) if step_wall_time is not None else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("target")
    parser.add_argument("target_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    report = estimate(os.path.abspath(args.target), args.target_args)
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(report, output)


if __name__ == "__main__":
    main()