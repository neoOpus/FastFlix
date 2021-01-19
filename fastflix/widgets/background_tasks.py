#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen, run

from qtpy import QtCore

from fastflix.language import t
from fastflix.models.fastflix_app import FastFlixApp

logger = logging.getLogger("fastflix")

__all__ = ["ThumbnailCreator", "ExtractSubtitleSRT", "SubtitleFix"]


class ThumbnailCreator(QtCore.QThread):
    def __init__(self, main, command=""):
        super().__init__(main)
        self.main = main
        self.command = command

    def run(self):
        self.main.thread_logging_signal.emit(f"INFO:{t('Generating thumbnail')}: {self.command}")
        result = run(self.command, stdin=PIPE, stdout=PIPE, stderr=STDOUT, shell=True)
        if result.returncode > 0:
            if "No such filter: 'zscale'" in result.stdout.decode(encoding="utf-8", errors="ignore"):
                self.main.thread_logging_signal.emit(
                    "ERROR:Could not generate thumbnail because you are using an outdated FFmpeg! "
                    "Please use FFmpeg 4.3+ built against the latest zimg libraries. "
                    "Static builds available at https://ffmpeg.org/download.html "
                    "(Linux distributions are often slow to update)"
                )
            else:
                self.main.thread_logging_signal.emit(f"ERROR:{t('Could not generate thumbnail')}: {result.stdout}")

            self.main.thumbnail_complete.emit(0)
        else:
            self.main.thumbnail_complete.emit(1)


class SubtitleFix(QtCore.QThread):
    def __init__(self, main, mkv_prop_edit, video_path):
        super().__init__(main)
        self.main = main
        self.mkv_prop_edit = mkv_prop_edit
        self.video_path = video_path

    def run(self):
        output_file = str(self.video_path).replace("\\", "/")
        self.main.thread_logging_signal.emit(f'INFO:{t("Will fix first subtitle track to not be default")}')
        try:
            result = run(
                [self.mkv_prop_edit, output_file, "--edit", "track:s1", "--set", "flag-default=0"],
                stdout=PIPE,
                stderr=STDOUT,
            )
        except Exception as err:
            self.main.thread_logging_signal.emit(f'ERROR:{t("Could not fix first subtitle track")} - {err}')
        else:
            if result.returncode != 0:
                self.main.thread_logging_signal.emit(
                    f'WARNING:{t("Could not fix first subtitle track")}: {result.stdout}'
                )


class ExtractSubtitleSRT(QtCore.QThread):
    def __init__(self, app: FastFlixApp, main, index, signal):
        super().__init__(main)
        self.main = main
        self.app = app
        self.index = index
        self.signal = signal

    def run(self):
        filename = str(Path(self.main.output_video).parent / f"{self.main.output_video}.{self.index}.srt").replace(
            "\\", "/"
        )
        self.main.thread_logging_signal.emit(f'INFO:{t("Extracting subtitles to")} {filename}')

        try:
            result = run(
                [
                    self.app.fastflix.config.ffmpeg,
                    "-y",
                    "-i",
                    self.main.input_video,
                    "-map",
                    f"0:{self.index}",
                    "-c",
                    "srt",
                    "-f",
                    "srt",
                    filename,
                ],
                stdout=PIPE,
                stderr=STDOUT,
            )
        except Exception as err:
            self.main.thread_logging_signal.emit(f'ERROR:{t("Could not extract subtitle track")} {self.index} - {err}')
        else:
            if result.returncode != 0:
                self.main.thread_logging_signal.emit(
                    f'WARNING:{t("Could not extract subtitle track")} {self.index}: {result.stdout}'
                )
            else:
                self.main.thread_logging_signal.emit(f'INFO:{t("Extracted subtitles successfully")}')
        self.signal.emit()


class ExtractHDR10(QtCore.QThread):
    def __init__(self, app: FastFlixApp, main, signal, stop_signal):
        super().__init__(main)
        self.main = main
        self.app = app
        self.signal = signal
        self.stop_signal = stop_signal
        self.process_one = None
        self.process_two = None
        self.stop_signal.connect(self.stop)
        self.stopped = False

    def stop(self):
        self.stopped = True
        try:
            self.process_two.terminate()
        except Exception as err:
            self.main.thread_logging_signal.emit(f"ERROR:{err}")
        try:
            self.process_one.terminate()
        except Exception as err:
            self.main.thread_logging_signal.emit(f"ERROR:{err}")

    def run(self):

        output = self.app.fastflix.current_video.work_path / "metadata.json"

        self.main.thread_logging_signal.emit(f'INFO:{t("Extracting HDR10+ metadata")} to {output}')

        try:
            self.process_one = Popen(
                [
                    self.app.fastflix.config.ffmpeg,
                    "-y",
                    "-i",
                    str(self.app.fastflix.current_video.source).replace("\\", "/"),
                    "-map",
                    f"0:{self.app.fastflix.current_video.video_settings.selected_track}",
                    "-loglevel",
                    "panic",
                    "-c:v",
                    "copy",
                    "-vbsf",
                    "hevc_mp4toannexb",
                    "-f",
                    "hevc",
                    "-",
                ],
                stdout=PIPE,
                stderr=PIPE,
                stdin=PIPE,  # FFmpeg can try to read stdin and wrecks havoc
            )

            self.process_two = Popen(
                ["hdr10plus_parser", "-o", str(output).replace("\\", "/"), "-"],
                stdout=PIPE,
                stderr=PIPE,
                stdin=self.process_one.stdout,
                encoding="utf-8",
                cwd=str(self.app.fastflix.current_video.work_path),
            )

            stdout, stderr = self.process_two.communicate()
        except Exception as err:
            self.main.thread_logging_signal.emit(f"ERROR: HDR10+ Extract error: {err}")
            self.signal.emit(f"ERROR|{err}")
        else:
            if self.stopped:
                self.signal.emit(f"STOPPED|STOPPED")
                return
            self.main.thread_logging_signal.emit(f"DEBUG: HDR10+ Extract output: {stdout}")
            if stderr:
                self.main.thread_logging_signal.emit(f"ERROR: HDR10+ Extract error: {stderr}")
            if self.process_two.poll() == 0:
                self.signal.emit(f"COMPLETE|{str(output)}")
            else:
                self.signal.emit(f"ERRORCODE|{self.process_two.poll()}")
