"""Steps 6-7: taglio, formato verticale 9:16 che segue il volto e sottotitoli parola per parola."""

from pathlib import Path

from .facetrack import plan_crop, write_sendcmd
from .ingest import run_ffmpeg
from .scoring import Clip
from .transcribe import Segment, Word

WIDTH, HEIGHT = 1080, 1920
WORDS_PER_LINE = 3

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial Black,88,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Hook,Arial Black,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,4,0,8,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def render_clip(
    source: Path, clip: Clip, segments: list[Segment], clips_dir: Path, work_dir: Path, index: int,
    track_faces: bool = True,
) -> Path:
    """Scrive la clip in clips_dir; i file di supporto (.ass, .cmd) restano in work_dir."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    words = [
        w for s in segments[clip.first_segment: clip.last_segment + 1] for w in s.words
        if w.end > clip.start and w.start < clip.end
    ]
    ass_name = f"clip_{index:02d}.ass"
    (work_dir / ass_name).write_text(build_ass(words, clip), encoding="utf-8")

    out_path = clips_dir / f"clip_{index:02d}.mp4"
    # ffmpeg gira dentro work_dir così i percorsi di .ass e .cmd nel filtro non hanno spazi da escapare.
    reframe = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"
    if track_faces:
        plan = plan_crop(source, clip.start, clip.end)
        if plan.crop_w < plan.src_w:
            cmd_name = f"clip_{index:02d}.cmd"
            write_sendcmd(plan, work_dir / cmd_name, clip.end - clip.start)
            reframe = (
                f"sendcmd=f={cmd_name},"
                f"crop=w={plan.crop_w}:h={plan.crop_h}:x={plan.xs[0][1]}:y=0,"
                f"scale={WIDTH}:{HEIGHT}"
            )
        if not plan.faces_found:
            print(f"  clip {index}: nessun volto trovato, crop centrale")
    vf = f"{reframe},setsar=1,ass={ass_name}"
    run_ffmpeg([
        "-ss", f"{clip.start:.3f}", "-to", f"{clip.end:.3f}", "-i", str(source),
        "-vf", vf,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(out_path),
    ], cwd=work_dir)
    return out_path


def build_ass(words: list[Word], clip: Clip) -> str:
    """Righe di WORDS_PER_LINE parole; la parola pronunciata si colora (effetto karaoke \\k)."""
    lines = [ASS_HEADER]
    if clip.hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(2.5)},Hook,,0,0,0,,{_escape(clip.hook.upper())}")

    for i in range(0, len(words), WORDS_PER_LINE):
        group = words[i: i + WORDS_PER_LINE]
        start = max(group[0].start - clip.start, 0)
        end = group[-1].end - clip.start
        parts, cursor = [], start
        for w in group:
            rel_start, rel_end = w.start - clip.start, w.end - clip.start
            gap = max(rel_start - cursor, 0)
            if gap > 0.01:
                parts.append(f"{{\\k{round(gap * 100)}}}")
            parts.append(f"{{\\k{max(round((rel_end - max(rel_start, cursor)) * 100), 1)}}}{_escape(w.text.upper())} ")
            cursor = max(rel_end, cursor)
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{''.join(parts).rstrip()}")
    return "\n".join(lines) + "\n"


def _ts(seconds: float) -> str:
    cs = round(max(seconds, 0) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")
