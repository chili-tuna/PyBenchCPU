"""
PyBench.py
------------------------------------------------------------
10 초 exp_inverse_sum CPU 벤치마크
- 싱글 / 멀티 코어 선택
- 진행률 바 + 상태 텍스트
- 최근 싱글 / 멀티 점수(반복 수·초당 반복)
- Stop 버튼으로 중단 가능
- WMI → 레지스트리 → platform 순으로 CPU 이름·코어·스레드 표시
- WMI 로 RAM 슬롯별 BankLabel / PartNumber / 용량 / 설정 클럭 표시
- 우측 하단 “Created by” 푸터
PyInstaller 패키징 예시
    pyinstaller --onefile --noconsole PyBench.py
(Windows / Python 3.10.11 기준)
"""

# ----------------------------------------------------------------------
# import
# ----------------------------------------------------------------------
from __future__ import annotations

import concurrent
import math
import multiprocessing as mp
import os
import platform
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor
from tkinter import ttk

# WMI (Windows 전용) ----------------------------------------------------
try:
    import wmi
except ImportError:
    wmi = None

# ----------------------------------------------------------------------
# 버전 정보
# ----------------------------------------------------------------------
__version__ = "1.0.1"

# ----------------------------------------------------------------------
# 설정 상수
# ----------------------------------------------------------------------
DURATION_SEC: float = 10.0  # 벤치마크 실행 시간
BATCH_SIZE: int = 100_000  # exp_inverse_sum 한 번에 계산할 개수

# ----------------------------------------------------------------------
# 멀티프로세싱 컨텍스트 / Event 헬퍼
# ----------------------------------------------------------------------
ctx = mp.get_context("spawn")

def new_event() -> mp.Event:
    """벤치마크 1회 실행용 Event 객체 생성"""
    return ctx.Event()

# ----------------------------------------------------------------------
# 벤치마크 계산 함수
# ----------------------------------------------------------------------
def exp_inverse_sum(start: int, end: int) -> float:
    """start ≤ i < end 범위에서 Σ e^(-i)"""
    return sum(math.exp(-i) for i in range(start, end))

# ----------------------------------------------------------------------
# 멀티 프로세스 워커
# ----------------------------------------------------------------------
_STOP_EVT: mp.Event | None = None  # 각 워커에서 참조할 전역 이벤트

def init_worker(ev: mp.Event) -> None:
    """워커 프로세스 초기화 시 전역 이벤트 주입"""
    global _STOP_EVT
    _STOP_EVT = ev

def exp_worker(offset: int) -> int:
    """워커 프로세스 본체 – 지정 시간 동안 반복 횟수 계산"""
    start_val = offset * 10_000_000 + 1
    iterations, t0 = 0, time.perf_counter()
    while (time.perf_counter() - t0) < DURATION_SEC:
        block_end = time.perf_counter() + 0.001  # 1 ms 블록
        while time.perf_counter() < block_end:
            exp_inverse_sum(start_val, start_val + BATCH_SIZE)
            iterations += 1
            start_val += BATCH_SIZE
        if _STOP_EVT and _STOP_EVT.is_set():
            break
    return iterations

# ----------------------------------------------------------------------
# 싱글 / 멀티 코어 실행 래퍼
# ----------------------------------------------------------------------
def run_single_core(stop_event: threading.Event) -> int:
    """메인 프로세스(싱글 코어)로 벤치마크 실행"""
    start_val, iterations = 1, 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < DURATION_SEC:
        if stop_event.is_set():
            break
        exp_inverse_sum(start_val, start_val + BATCH_SIZE)
        iterations += 1
        start_val += BATCH_SIZE
    return iterations

def run_multi_core(stop_event: threading.Event, stop_evt_mp: mp.Event) -> int:
    """논리 코어 수만큼 프로세스 생성 후 병렬 벤치마크 실행"""
    cores = mp.cpu_count()
    with ProcessPoolExecutor(max_workers=cores,
                             mp_context=ctx,
                             initializer=init_worker,
                             initargs=(stop_evt_mp,)) as exe:
        futures = [exe.submit(exp_worker, i) for i in range(cores)]
        total = 0
        try:
            for f in concurrent.futures.as_completed(futures):
                if stop_event.is_set():
                    stop_evt_mp.set()  # GUI → 워커 브로드캐스트
                if stop_evt_mp.is_set():
                    break
                total += f.result()
        finally:
            # Stop이 눌린 경우 미완료 Future 취소 및 풀 즉시 종료
            exe.shutdown(wait=False, cancel_futures=True)
        return total

# ----------------------------------------------------------------------
# CPU / RAM 정보 헬퍼
# ----------------------------------------------------------------------
def _cpu_name_from_registry() -> str | None:
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
    """'CPU 이름  |  xC / yT' 형식 문자열 반환"""
    name, physical, logical = None, "?", "?"
    if sys.platform.startswith("win") and wmi:
        try:
            cpu = wmi.WMI().Win32_Processor()[0]
            name = cpu.Name.strip()
            logical = cpu.NumberOfLogicalProcessors
            physical = cpu.NumberOfCores
        except Exception:
            pass
    if not name and sys.platform.startswith("win"):
        name = _cpu_name_from_registry()
    if not name:
        name = platform.processor() or platform.uname().processor or "Unknown CPU"
    if logical == "?":
        logical = os.cpu_count() or "?"
    return f"{name}  |  {physical}C / {logical}T"

def get_ram_info() -> str:
    """RAM 슬롯별 BankLabel / PartNumber / 용량 / 설정 클럭 문자열 반환"""
    if not (wmi and sys.platform.startswith("win")):
        return ""
    try:
        modules = wmi.WMI().Win32_PhysicalMemory()
    except Exception:
        return ""
    lines: list[str] = []
    for mem in modules:
        bank = mem.BankLabel or mem.DeviceLocator or "Unknown"
        part = (mem.PartNumber or "").strip() or "N/A"
        cap_gb = int(mem.Capacity) / (1024 ** 3)
        speed = mem.ConfiguredClockSpeed or mem.Speed or 0
        lines.append(f"{bank}  |  {part}  |  {int(cap_gb)} GB  |  {speed} MT/s")
    return "\n".join(lines)

# ----------------------------------------------------------------------
# Tkinter GUI
# ----------------------------------------------------------------------
class BenchmarkGUI(tk.Tk):
    """CPU / RAM 정보 + 벤치마크 실행 GUI"""

    # ------------------------- 초기화 -------------------------
    def __init__(self) -> None:
        super().__init__()
        self.title("Python CPU Benchmark")
        self.resizable(False, False)

        # ── 루트 프레임(20px 패딩) ────────────────────────────
        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)

        # 폰트 설정
        f_title  = ("Arial", 14, "bold")
        f_status = ("Arial", 12, "bold")
        f_cpu    = ("Arial", 12)
        f_ram    = ("Arial", 10)
        f_result = ("Arial", 12)

        # ── 제목 ------------------------------------------------
        ttk.Label(root, text=f"Select Test (runs {int(DURATION_SEC)} s)", font=f_title).pack(pady=(0, 12))

        # ── 실행 / 중단 버튼 -----------------------------------
        btn_f = ttk.Frame(root)
        btn_f.pack()
        self.single_btn = ttk.Button(btn_f, text="Single-core", command=self.start_single)
        self.multi_btn = ttk.Button(btn_f, text="Multi-core", command=self.start_multi)
        self.stop_btn = ttk.Button(btn_f, text="Stop", command=self.stop_benchmark, state=tk.DISABLED)
        self.single_btn.grid(row=0, column=0, padx=8)
        self.multi_btn.grid(row=0, column=1, padx=8)
        self.stop_btn.grid(row=0, column=2, padx=8)

        # ── 진행률 바 + 상태 -----------------------------------
        self.progress = ttk.Progressbar(root, mode="determinate", length=380, maximum=DURATION_SEC)
        self.progress.pack(pady=(14, 6))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var, font=f_status).pack()

        # ── CPU 정보 ------------------------------------------
        ttk.Label(root, text=get_cpu_info(), font=f_cpu).pack(pady=(6, 8))

        # ── RAM 정보 ------------------------------------------
        ram_text = get_ram_info()
        if ram_text:
            ttk.Label(root, text=ram_text, font=f_ram).pack(padx=4, pady=(0, 8))

        # ── 테스트 결과(2행 × 3열) -----------------------------
        ttk.Label(root, text="Results:", font=f_title).pack(anchor="w", padx=4)
        res_f = ttk.Frame(root)
        res_f.pack(fill="x", padx=16)

        self.last_single_iter = tk.StringVar(value="-")
        self.last_single_ips  = tk.StringVar(value="-")
        self.last_multi_iter  = tk.StringVar(value="-")
        self.last_multi_ips   = tk.StringVar(value="-")

        ttk.Label(res_f, text="Single-core", font=f_result,
                  anchor="w", width=10
                 ).grid(row=0, column=0, sticky="w")
        ttk.Label(res_f, textvariable=self.last_single_iter,
                  font=f_result, anchor="e", width=12
                 ).grid(row=0, column=1, sticky="e")
        ttk.Label(res_f, textvariable=self.last_single_ips,
                  font=f_result, anchor="e", width=10
                 ).grid(row=0, column=2, sticky="e")

        ttk.Label(res_f, text="Multi-core", font=f_result, anchor="w", width=10).grid(row=1, column=0, sticky="w")
        ttk.Label(res_f, textvariable=self.last_multi_iter, font=f_result, anchor="e", width=12).grid(row=1, column=1, sticky="e")
        ttk.Label(res_f, textvariable=self.last_multi_ips, font=f_result, anchor="e", width=10).grid(row=1, column=2, sticky="e")

        res_f.columnconfigure(1, weight=1)
        res_f.columnconfigure(2, weight=1)

        # ── 푸터 ------------------------------------------------
        ttk.Label(self, text=f"PyBench v{__version__}  |  Created by chili‑tuna", font=("Arial", 9)).pack(side="bottom", anchor="e", padx=10, pady=10)

        # 내부 상태
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stop_evt_mp: mp.Event | None = None

        # 창 중앙 배치
        self.update_idletasks()
        self._center_window()

    # ------------------ 버튼 콜백 ------------------
    def start_single(self) -> None: self._run_benchmark("Single")
    def start_multi(self) -> None:  self._run_benchmark("Multi")

    def stop_benchmark(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set()
            if self._stop_evt_mp:
                self._stop_evt_mp.set()
            self.status_var.set("Stopping…")

    # --------------- 벤치마크 실행 -----------------
    def _run_benchmark(self, mode: str) -> None:
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.DISABLED
        self.stop_btn["state"] = tk.NORMAL
        self.status_var.set("Running...: 0.0 s")
        self.progress["value"] = 0
        self._stop_event.clear()
        self._stop_evt_mp = new_event()

        start_time = time.perf_counter()

        # 실제 연산 스레드 --------------------------------------
        def task() -> None:
            if mode == "Single":
                iterations = run_single_core(self._stop_event)
            else:
                iterations = run_multi_core(self._stop_event, self._stop_evt_mp)
            elapsed = time.perf_counter() - start_time
            if not self._stop_event.is_set():
                ips = iterations / elapsed if elapsed else 0
                if mode == "Single":
                    self.last_single_iter.set(f"{iterations:,} iters")
                    self.last_single_ips.set(f"{ips:,.0f} it/s")
                else:
                    self.last_multi_iter.set(f"{iterations:,} iters")
                    self.last_multi_ips.set(f"{ips:,.0f} it/s")
            self.after(0, self._finish_benchmark)

        # 진행률 / 경과 시간 업데이트 ---------------------------
        def updater() -> None:
            if self._worker_thread and self._worker_thread.is_alive():
                elapsed = time.perf_counter() - start_time
                self.progress["value"] = min(elapsed, DURATION_SEC)
                self.status_var.set(f"Running...: {elapsed:4.1f} s")
                self.after(100, updater)

        self.after(100, updater)
        self._worker_thread = threading.Thread(target=task, daemon=True)
        self._worker_thread.start()

    # --------------- 벤치마크 종료 -----------------
    def _finish_benchmark(self) -> None:
        if self._worker_thread:
            self._worker_thread.join()  # 워커 완전 종료 보장
        self.progress["value"] = 0
        self.status_var.set("Finished!" if not self._stop_event.is_set() else "Cancelled")
        self.stop_btn["state"] = tk.DISABLED
        for b in (self.single_btn, self.multi_btn):
            b["state"] = tk.NORMAL

    # --------------- 창 중앙 정렬 -----------------
    def _center_window(self) -> None:
        """현재 창을 화면 중앙에 배치"""
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

# ----------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------
if __name__ == "__main__":
    mp.freeze_support()            # PyInstaller 윈도우즈 호환
    BenchmarkGUI().mainloop()
