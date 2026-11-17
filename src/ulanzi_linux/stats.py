"""CPU / RAM / GPU utilisation for the info window's statistics mode."""

from __future__ import annotations

import glob
import shutil
import subprocess


class Stats:
    def __init__(self) -> None:
        self._prev = self._cpu_times()
        self._gpu_files = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
        self._nvidia = bool(shutil.which("nvidia-smi")) and not self._gpu_files

    @staticmethod
    def _cpu_times() -> tuple[int, int]:
        with open("/proc/stat") as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals[:8])

    def cpu(self) -> int:
        idle, total = self._cpu_times()
        pi, pt = self._prev
        self._prev = (idle, total)
        dt = total - pt
        return max(0, min(100, round(100 * (1 - (idle - pi) / dt)))) if dt > 0 else 0

    @staticmethod
    def mem() -> int:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        total = info.get("MemTotal", 1)
        return round(100 * (total - info.get("MemAvailable", total)) / total)

    def gpu(self) -> int:
        try:
            if self._gpu_files:
                return max(int(open(f).read().strip() or 0) for f in self._gpu_files)
            if self._nvidia:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2,
                ).stdout
                return int(out.split()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        return 0

    def sample(self) -> tuple[int, int, int]:
        return self.cpu(), self.mem(), self.gpu()
