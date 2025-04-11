"""
cpu_benchmark_gui.py
------------------------------------------------------------
10 초 exp_inverse_sum CPU 벤치마크
- 싱글 / 멀티 코어 선택 가능
- 실시간 진행률 바 + 상태 텍스트
- 최근 싱글 / 멀티 점수 표시
- 중간 취소용 Stop 버튼
- WMI → 레지스트리 → platform 순으로 CPU 이름‧코어‧스레드 표시
- 우측 하단 “Created by” 푸터
Python 3.10.11 (Windows, spawn 방식)에서 테스트 완료
PyInstaller 패키징 예시:
    pyinstaller --onefile --noconsole cpu_benchmark_gui.py
"""

# ------------------------------- import -------------------------------
from __future__ import annotations

import math
import multiprocessing as mp
import os
import platform
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor

import tkinter as tk
from tkinter import ttk

# psutil은 선택 설치 (물리 코어 수 확인용) ------------------------------
try:
    import psutil
except ImportError:
    psutil = None

# ----------------------------------------------------------------------
# 설정 상수
# ----------------------------------------------------------------------
DURATION_SEC: float = 10.0  # 벤치마크 실행 시간(초)
BATCH_SIZE: int = 100_000  # exp_inverse_sum 한 번에 계산할 원소 수
CHECK_EVERY: int = 100  # stop 이벤트 확인 주기(반복 횟수)


# ----------------------------------------------------------------------
# 벤치마크 계산 함수
# ----------------------------------------------------------------------
def exp_inverse_sum(start: int, end: int) -> float:
    """start ≤ i < end 범위에서 Σ e^(−i) 반환"""
    return sum(math.exp(-i) for i in range(start, end))


# ----------------------------------------------------------------------
# 멀티 프로세스 워커 헬퍼
# ----------------------------------------------------------------------
_STOP_EVT: mp.Event | None = None  # 워커 프로세스에서 참조할 전역 변수


def init_worker(ev: mp.Event) -> None:
    """각 프로세스 시작 시 호출되어 전역 이벤트 주입"""
    global _STOP_EVT
    _STOP_EVT = ev


def exp_worker(offset: int) -> int:
    """
    워커 프로세스 본체.
    DURATION_SEC 동안 exp_inverse_sum 반복 수행 후
    완료한 반복 횟수를 반환.
    """
    start_val = offset * 10_000_000 + 1  # 프로세스별 계산 범위 분리
    iterations = 0
    t0 = time.perf_counter()

    while (time.perf_counter() - t0) < DURATION_SEC:
        if iterations % CHECK_EVERY == 0 and _STOP_EVT.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH_SIZE)
        iterations += 1
        start_val += BATCH_SIZE
    return iterations


# ----------------------------------------------------------------------
# 싱글 / 멀티 코어 래퍼
# ----------------------------------------------------------------------
def run_single_core(stop_event: threading.Event) -> int:
    """메인 프로세스에서 단일 코어 벤치마크 수행"""
    start_val = 1
    iterations = 0
    t0 = time.perf_counter()

    while time.perf_counter() - t0 < DURATION_SEC:
        if stop_event.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH_SIZE)
        iterations += 1
        start_val += BATCH_SIZE
    return iterations


def run_multi_core(stop_event: threading.Event) -> int:
    """논리 코어 수만큼 프로세스를 띄워 병렬 벤치마크 수행"""
    cores = mp.cpu_count()
    ctx = mp.get_context("spawn")  # Windows 호환
    stop_evt = ctx.Event()  # 피클링 가능한 Event

    # GUI 스레드의 stop_event 신호를 워커용 stop_evt로 릴레이
    threading.Thread(target=lambda: (stop_event.wait(), stop_evt.set()),
                     daemon=True).start()

    with ProcessPoolExecutor(max_workers=cores,
                             mp_context=ctx,
                             initializer=init_worker,
                             initargs=(stop_evt,)) as exe:
        futures = [exe.submit(exp_worker, i) for i in range(cores)]
        return sum(f.result() for f in futures)


# ----------------------------------------------------------------------
# CPU 정보 헬퍼
# ----------------------------------------------------------------------
def _cpu_name_windows_registry() -> str | None:
    """레지스트리에서 ProcessorNameString 읽기(백업용)"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        return None


def get_cpu_info() -> str:
    """
    'CPU 이름  |  xC / yT' 형식 문자열 반환
    우선순위: WMI → 레지스트리 → platform 모듈
    """
    # 1) WMI (작업 관리자와 동일한 이름 확보)
    if sys.platform.startswith("win"):
        try:
            import wmi
            cpu = wmi.WMI().Win32_Processor()[0]
            name = cpu.Name.strip()
            logical = int(cpu.NumberOfLogicalProcessors)
            physical = int(cpu.NumberOfCores)
            return f"{name}  |  {physical}C / {logical}T"
        except Exception:
            pass  # 실패 시 다음 방법으로 이동

    # 2) 레지스트리(Windows 백업)
    name = _cpu_name_windows_registry() if sys.platform.startswith("win") else None

    # 3) platform 모듈(최종 백업)
    if not name:
        name = platform.processor() or platform.uname().processor or "Unknown CPU"

    logical = os.cpu_count() or "?"
    physical = psutil.cpu_count(logical=False) if psutil else "?"

    return f"{name}  |  {physical}C / {logical}T"


# ----------------------------------------------------------------------
# Tkinter GUI
# ----------------------------------------------------------------------
class BenchmarkGUI(tk.Tk):
    """
    간단한 CPU 벤치마크 GUI
    - 중앙 정렬된 콘텐츠
    - 진행률 바 + 상태 텍스트
    - 최근 싱글/멀티 점수
    - Stop 버튼으로 중단 가능
    """

    # ----------------------- 초기화 -----------------------------
    def __init__(self) -> None:
        super().__init__()
        self.title("Python CPU Benchmark")
        self.geometry("500x320")
        self.resizable(False, False)
        self._center_window()

        # 중앙 배치용 루트 프레임 --------------------------------
        root = ttk.Frame(self)
        root.place(relx=0.5, rely=0.5, anchor="center")

        # 폰트 설정 ---------------------------------------------
        title_font = ("Segoe UI", 14, "bold")
        status_font = ("Segoe UI", 12, "bold")
        cpu_font = ("Segoe UI", 12)
        result_font = ("Segoe UI", 12)

        # 제목 ---------------------------------------------------
        ttk.Label(root,
                  text=f"Select Test (runs {int(DURATION_SEC)} s)",
                  font=title_font).pack(pady=(0, 12))

        # 버튼 ---------------------------------------------------
        btn_frame = ttk.Frame(root)
        btn_frame.pack()
        self.single_btn = ttk.Button(btn_frame, text="Single‑core",
                                     command=self.start_single)
        self.multi_btn = ttk.Button(btn_frame, text="Multi‑core",
                                    command=self.start_multi)
        self.stop_btn = ttk.Button(btn_frame, text="Stop",
                                   command=self.stop_benchmark,
                                   state=tk.DISABLED)
        self.single_btn.grid(row=0, column=0, padx=8)
        self.multi_btn.grid(row=0, column=1, padx=8)
        self.stop_btn.grid(row=0, column=2, padx=8)

        # 진행률 바 + 상태 ---------------------------------------
        self.progress = ttk.Progressbar(root, mode="determinate",
                                        length=380, maximum=DURATION_SEC)
        self.progress.pack(pady=(14, 6))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var,
                  font=status_font).pack()

        # CPU 정보 ----------------------------------------------
        ttk.Label(root, text=get_cpu_info(), font=cpu_font).pack(pady=(6, 12))

        # 최근 결과 ---------------------------------------------
        ttk.Label(root, text="Results:", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=4)
        res_frame = ttk.Frame(root)
        res_frame.pack(anchor="w", padx=16)
        self.last_single = tk.StringVar(value="Single : -")
        self.last_multi = tk.StringVar(value="Multi  : -")
        ttk.Label(res_frame, textvariable=self.last_single,
                  font=result_font).pack(anchor="w")
        ttk.Label(res_frame, textvariable=self.last_multi,
                  font=result_font).pack(anchor="w")

        # 제작자 푸터 -------------------------------------------
        ttk.Label(self, text="Created by chili‑tuna",
                  font=("Segoe UI", 9)
                  ).place(relx=1.0, rely=1.0, anchor="se", x=-6, y=-6)

        # 내부 상태 변수 ----------------------------------------
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------- 버튼 핸들러 ---------------------------
    def start_single(self) -> None:
        self._run_benchmark("Single")

    def start_multi(self) -> None:
        self._run_benchmark("Multi")

    def stop_benchmark(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set()
            self.status_var.set("Stopping...")

    # ----------------- 벤치마크 실행 ---------------------------
    def _run_benchmark(self, mode: str) -> None:
        # 버튼 상태 전환
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.DISABLED
        self.stop_btn["state"] = tk.NORMAL

        # UI 초기화
        self.status_var.set("Running...: 0.0 s")
        self.progress["value"] = 0
        self._stop_event.clear()

        start_time = time.perf_counter()

        # 실제 계산 스레드 --------------------------------------
        def task() -> None:
            iterations = (
                run_single_core(self._stop_event)
                if mode == "Single" else
                run_multi_core(self._stop_event)
            )
            elapsed = time.perf_counter() - start_time

            if self._stop_event.is_set():
                score = "Cancelled"
            else:
                ips = iterations / elapsed if elapsed else 0
                score = f"{iterations:,} iter  |  {ips:,.0f} it/s"
                if mode == "Single":
                    self.last_single.set(f"Single : {score}")
                else:
                    self.last_multi.set(f"Multi  : {score}")

            self.after(0, self._finish_benchmark, score)

        # 진행률 및 경과 시간 업데이트 ---------------------------
        def updater() -> None:
            if self._worker_thread and self._worker_thread.is_alive():
                elapsed = time.perf_counter() - start_time
                self.progress["value"] = min(elapsed, DURATION_SEC)
                self.status_var.set(f"Running...: {elapsed:4.1f} s")
                self.after(100, updater)  # 0.1초 간격

        self.after(100, updater)
        self._worker_thread = threading.Thread(target=task, daemon=True)
        self._worker_thread.start()

    # ----------------- 벤치마크 종료 ---------------------------
    def _finish_benchmark(self, score: str) -> None:
        self.progress["value"] = 0
        self.status_var.set("Finished!" if score != "Cancelled" else "Cancelled")
        self.stop_btn["state"] = tk.DISABLED
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.NORMAL

    # ----------------- 창 중앙 정렬 ---------------------------
    def _center_window(self) -> None:
        """초기 창을 화면 중앙에 배치"""
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"+{x}+{y}")


# ----------------------------------------------------------------------
# 메인 엔트리포인트
# ----------------------------------------------------------------------
if __name__ == "__main__":
    mp.freeze_support()  # PyInstaller 윈도우즈 호환
    BenchmarkGUI().mainloop()
