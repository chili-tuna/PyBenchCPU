import time
import math
import threading
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import tkinter as tk
from tkinter import ttk

# -------- 설정 --------
DURATION = 10.0         # 벤치마크 실행 시간 (초)
BATCH    = 50_000       # 한 번에 계산할 원소 수
CHECK_EVERY = 100       # stop 이벤트 주기적 확인

# -------- 계산 함수 --------
def exp_inverse_sum(start, end):
    return sum(math.exp(-i) for i in range(start, end))

# -------- 실행 래퍼 --------
_STOP_EVT = None                      # 각 프로세스에서 공유할 전역 변수

def init_worker(ev):
    global _STOP_EVT
    _STOP_EVT = ev                    # Event proxy → 로컬 변수에 저장

def exp_worker(offset):
    start_val = offset * 10_000_000 + 1
    iterations = 0
    t0 = time.perf_counter()

    while (time.perf_counter() - t0) < DURATION:
        # 중단 요청 주기적 확인
        if iterations % CHECK_EVERY == 0 and _STOP_EVT.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH)
        iterations += 1
        start_val  += BATCH
    return iterations

def run_single_core(stop_event: threading.Event) -> int:
    start_time = time.time()
    iterations = 0
    start = 1
    while time.time() - start_time < DURATION:
        if stop_event.is_set():
            break
        exp_inverse_sum(start, start + BATCH)
        iterations += 1
        start += BATCH
    return iterations

def run_multi_core(stop_event: threading.Event) -> int:
    cores = mp.cpu_count()
    ctx   = mp.get_context("spawn")      # Windows 호환

    stop_evt = ctx.Event()               # picklable!

    # GUI 스레드 → stop_event → stop_evt 로 전달
    threading.Thread(target=lambda: (stop_event.wait(), stop_evt.set()),
                     daemon=True).start()

    with ProcessPoolExecutor(max_workers=cores,
                             mp_context=ctx,
                             initializer=init_worker,
                             initargs=(stop_evt,)) as exe:
        futures = [exe.submit(exp_worker, i) for i in range(cores)]
        return sum(f.result() for f in futures)


# -------- GUI --------
class BenchmarkGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Python CPU Benchmark")
        self.geometry("400x230")
        self.resizable(False, False)

        ttk.Label(self, text=f"Select mode (runs {int(DURATION)} s):",
                  font=("Segoe UI", 12)).pack(pady=12)

        # 버튼 영역
        btn_frame = ttk.Frame(self)
        btn_frame.pack()
        self.single_btn = ttk.Button(btn_frame, text="Single‑core",
                                     command=self.start_single)
        self.multi_btn  = ttk.Button(btn_frame, text="Multi‑core",
                                     command=self.start_multi)
        self.stop_btn   = ttk.Button(btn_frame, text="Stop",
                                     command=self.stop_benchmark, state=tk.DISABLED)
        self.single_btn.grid(row=0, column=0, padx=6)
        self.multi_btn.grid(row=0, column=1, padx=6)
        self.stop_btn.grid(row=0, column=2, padx=6)

        # 진행률 / 경과 시간
        self.progress = ttk.Progressbar(self, mode="determinate",
                                        length=300, maximum=DURATION)
        self.progress.pack(pady=10)
        self.time_var = tk.StringVar(value="Elapsed: 0.0 s")
        ttk.Label(self, textvariable=self.time_var).pack()

        # 결과
        self.result_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.result_var,
                  font=("Consolas", 11)).pack(pady=10)

        # 내부 상태
        self._worker_thread: threading.Thread | None = None
        self._stop_event   = threading.Event()

    # --- 벤치마크 시작/중단 ---
    def start_single(self): self._run_benchmark("single")
    def start_multi(self):  self._run_benchmark("multi")

    def stop_benchmark(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set()   # 워커에게 중단 신호
            self.result_var.set("Stopping...")

    def _run_benchmark(self, mode: str):
        # UI 상태 전환
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.DISABLED
        self.stop_btn["state"] = tk.NORMAL
        self.result_var.set("Running...")
        self.progress["value"] = 0
        self._stop_event.clear()

        start_time = time.time()

        def task():
            if mode == "single":
                iterations = run_single_core(self._stop_event)
            else:
                iterations = run_multi_core(self._stop_event)

            elapsed = time.time() - start_time
            ips = iterations / elapsed if elapsed else 0
            # 간소화된 점수(총 iteration) + it/s
            score = f"{iterations:,} iter   |   {ips:,.0f} it/s"
            self.after(0, self._finish_benchmark, score)

        # 진행률/시간 업데이트 타이머
        def updater():
            if self._worker_thread and self._worker_thread.is_alive():
                elapsed = time.time() - start_time
                self.progress["value"] = min(elapsed, DURATION)
                self.time_var.set(f"Elapsed: {elapsed:4.1f} s")
                self.after(100, updater)  # 0.1 s 간격
        self.after(100, updater)

        self._worker_thread = threading.Thread(target=task, daemon=True)
        self._worker_thread.start()

    def _finish_benchmark(self, score: str):
        self.progress["value"] = 0
        self.time_var.set("Elapsed: 0.0 s")
        self.result_var.set(score)
        self.stop_btn["state"] = tk.DISABLED
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.NORMAL

# -------- 메인 --------
if __name__ == "__main__":
    mp.freeze_support()
    BenchmarkGUI().mainloop()
