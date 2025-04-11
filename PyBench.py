"""
cpu_benchmark_gui.py
---------------------------------
10‑second exp_inverse_sum CPU benchmark
(single‑core / multi‑core, session log, stop‑button support)
Tested on Python 3.10.11 (Windows spawn OK)
"""

from __future__ import annotations  # 파이썬 3.10에서 타입 힌트 순환 참조 대비
import time
import math
import threading
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import tkinter as tk
from tkinter import ttk
import platform
import os
import datetime

# psutil은 선택 사항 → 없을 때도 동작하도록 예외 처리
try:
    import psutil
except ImportError:   # psutil 미설치 시 graceful degradation
    psutil = None

# ----------------------------------------------------------------
# 설정 상수
# ----------------------------------------------------------------
DURATION_SEC: float = 10.0         # 벤치마크 실행 시간
BATCH_SIZE: int = 100_000          # exp_inverse_sum 한 번에 처리할 개수
CHECK_EVERY: int = 100             # stop 이벤트 확인 주기

# ----------------------------------------------------------------
# 벤치마크용 계산 함수
# ----------------------------------------------------------------
def exp_inverse_sum(start: int, end: int) -> float:
    """Σ e^(-i) (start ≤ i < end)"""
    return sum(math.exp(-i) for i in range(start, end))

# ----------------------------------------------------------------
# 다중 프로세스 워커 초기화 / 본체
# ----------------------------------------------------------------
_STOP_EVT = None  # 프로세스 전역 Event (spawn 시 전달)

def init_worker(ev: mp.Event) -> None:
    """각 프로세스가 시작할 때 호출되어 전역 _STOP_EVT 설정"""
    global _STOP_EVT
    _STOP_EVT = ev

def exp_worker(offset: int) -> int:
    """
    duration 동안 exp_inverse_sum 반복 수행
    offset은 프로세스별 시작 위치 분리용
    """
    start_val = offset * 10_000_000 + 1
    iterations = 0
    t0 = time.perf_counter()

    while (time.perf_counter() - t0) < DURATION_SEC:
        if iterations % CHECK_EVERY == 0 and _STOP_EVT.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH_SIZE)
        iterations += 1
        start_val += BATCH_SIZE
    return iterations

# ----------------------------------------------------------------
# 싱글/멀티‑코어 실행 래퍼
# ----------------------------------------------------------------
def run_single_core(stop_event: threading.Event) -> int:
    """단일 스레드(프로세스)로 DURATION_SEC 동안 실행"""
    start_time = time.perf_counter()
    iterations = 0
    start_val = 1

    while time.perf_counter() - start_time < DURATION_SEC:
        if stop_event.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH_SIZE)
        iterations += 1
        start_val += BATCH_SIZE
    return iterations

def run_multi_core(stop_event: threading.Event) -> int:
    """CPU 코어 수만큼 프로세스를 띄워 병렬 실행"""
    cores = mp.cpu_count()
    ctx = mp.get_context("spawn")           # Windows 호환
    stop_evt = ctx.Event()                  # picklable Event

    # GUI 스레드의 stop_event를 멀티 프로세스용 stop_evt로 릴레이
    threading.Thread(
        target=lambda: (stop_event.wait(), stop_evt.set()),
        daemon=True
    ).start()

    with ProcessPoolExecutor(
        max_workers=cores,
        mp_context=ctx,
        initializer=init_worker,
        initargs=(stop_evt,)
    ) as exe:
        futures = [exe.submit(exp_worker, i) for i in range(cores)]
        return sum(f.result() for f in futures)

# ----------------------------------------------------------------
# 시스템 정보 유틸
# ----------------------------------------------------------------
def get_cpu_info() -> str:
    """CPU 모델명 및 물리/논리 코어 수 반환"""
    logical = os.cpu_count() or "?"
    physical = psutil.cpu_count(logical=False) if psutil else "?"
    name = platform.processor() or platform.uname().processor or "Unknown CPU"
    return f"{name}  |  {physical}C / {logical}T"

# ----------------------------------------------------------------
# GUI 클래스
# ----------------------------------------------------------------
class BenchmarkGUI(tk.Tk):
    """Tkinter 기반 간단한 벤치마크 GUI"""

    def __init__(self) -> None:
        super().__init__()
        self.title("Python CPU Benchmark")
        self.geometry("440x300")
        self.resizable(False, False)
        self._center_window()  # 창 중앙 배치

        # UI 구성 -------------------------------------------------
        ttk.Label(
            self,
            text=f"Select mode (runs {int(DURATION_SEC)} s):",
            font=("Segoe UI", 12)
        ).pack(pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack()
        self.single_btn = ttk.Button(btn_frame, text="Single‑core",
                                     command=self.start_single)
        self.multi_btn = ttk.Button(btn_frame, text="Multi‑core",
                                    command=self.start_multi)
        self.stop_btn = ttk.Button(btn_frame, text="Stop",
                                   command=self.stop_benchmark,
                                   state=tk.DISABLED)
        self.single_btn.grid(row=0, column=0, padx=6)
        self.multi_btn.grid(row=0, column=1, padx=6)
        self.stop_btn.grid(row=0, column=2, padx=6)

        # 진행률 / 경과 시간 -------------------------------------
        self.progress = ttk.Progressbar(
            self, mode="determinate", length=340, maximum=DURATION_SEC
        )
        self.progress.pack(pady=8)
        self.time_var = tk.StringVar(value="Elapsed: 0.0 s")
        ttk.Label(self, textvariable=self.time_var).pack()

        # CPU 정보 ----------------------------------------------
        ttk.Label(self, text=get_cpu_info(), foreground="#555").pack(pady=2)

        # 결과 표시 ----------------------------------------------
        self.result_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.result_var,
                  font=("Consolas", 11)).pack(pady=6)

        # 실행 기록 ---------------------------------------------
        ttk.Label(self, text="Run history (this session):").pack()
        self.history_box = tk.Listbox(self, height=5, width=60)
        self.history_box.pack(pady=(0, 6))

        # 내부 상태 변수 -----------------------------------------
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # 버튼 핸들러 ------------------------------------------------
    def start_single(self) -> None:
        self._run_benchmark("Single")

    def start_multi(self) -> None:
        self._run_benchmark("Multi")

    def stop_benchmark(self) -> None:
        """Stop 버튼 클릭 시 호출"""
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set()
            self.result_var.set("Stopping...")

    # 벤치마크 실행 로직 ----------------------------------------
    def _run_benchmark(self, mode: str) -> None:
        # UI 잠금
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.DISABLED
        self.stop_btn["state"] = tk.NORMAL
        self.result_var.set("Running...")
        self.progress["value"] = 0
        self._stop_event.clear()

        start_time = time.perf_counter()

        # 실제 벤치마크를 백그라운드 스레드로 실행
        def task() -> None:
            iterations = (
                run_single_core(self._stop_event)
                if mode == "Single" else
                run_multi_core(self._stop_event)
            )

            elapsed = time.perf_counter() - start_time
            if self._stop_event.is_set():         # 중단된 경우
                score = "Cancelled"
            else:
                ips = iterations / elapsed if elapsed else 0
                score = f"{iterations:,} iter   |   {ips:,.0f} it/s"

                # 세션 기록 추가
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                self.history_box.insert(
                    tk.END, f"[{timestamp}] {mode} : {score}"
                )
                self.history_box.yview_moveto(1.0)

            self.after(0, self._finish_benchmark, score)

        # 진행률 / 경과 시간 업데이트
        def updater() -> None:
            if self._worker_thread and self._worker_thread.is_alive():
                elapsed = time.perf_counter() - start_time
                self.progress["value"] = min(elapsed, DURATION_SEC)
                self.time_var.set(f"Elapsed: {elapsed:4.1f} s")
                self.after(100, updater)  # 100 ms 간격

        self.after(100, updater)
        self._worker_thread = threading.Thread(target=task, daemon=True)
        self._worker_thread.start()

    # 벤치마크 종료 후 UI 복원 ----------------------------------
    def _finish_benchmark(self, score: str) -> None:
        self.progress["value"] = 0
        self.time_var.set("Elapsed: 0.0 s")
        self.result_var.set(score)
        self.stop_btn["state"] = tk.DISABLED
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.NORMAL

    # 창 중앙 정렬 ---------------------------------------------
    def _center_window(self) -> None:
        """현재 창을 화면 정중앙에 배치"""
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"+{x}+{y}")

# ----------------------------------------------------------------
# 메인 엔트리포인트
# ----------------------------------------------------------------
if __name__ == "__main__":
    mp.freeze_support()  # PyInstaller 윈도우즈 호환
    BenchmarkGUI().mainloop()
