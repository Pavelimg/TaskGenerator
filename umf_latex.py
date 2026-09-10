from __future__ import annotations

import csv
import json
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_DIR = Path(__file__).resolve().parent
TASKS_DIR = PROJECT_DIR / "Задания"
SCHEMES_DIR = PROJECT_DIR / "Схемы"
RESULTS_DIR = PROJECT_DIR / "Результаты"

ANSWER_RE = re.compile(r"^\s*%\s*Ответ\s*:\s*(.*?)\s*$", re.IGNORECASE)
DIFFICULTY_RE = re.compile(r"^\s*%\s*Сложность\s*:\s*(.*?)\s*$", re.IGNORECASE)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ProjectError(RuntimeError):
    """Понятная пользователю ошибка в структуре проекта или входных данных."""


@dataclass(frozen=True)
class Task:
    subject: str
    theme: str
    name: str
    path: Path
    body: str
    answer: str | None
    difficulty: str | None


@dataclass(frozen=True)
class Issue:
    level: str
    path: Path
    message: str


@dataclass(frozen=True)
class Scheme:
    name: str
    subject: str
    themes: dict[str, int]
    path: Path


@dataclass(frozen=True)
class OutputBundle:
    directory: Path
    tex_files: tuple[Path, ...]
    other_files: tuple[Path, ...]


Catalog = dict[str, dict[str, list[Task]]]


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def safe_name(value: str, fallback: str = "Без названия") -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value).strip(" .")
    cleaned = cleaned or fallback
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:120].rstrip(" .") or fallback


def tex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _trim_empty_edges(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def parse_task_file(path: Path, tasks_root: Path = TASKS_DIR) -> tuple[Task, list[Issue]]:
    try:
        relative = path.relative_to(tasks_root)
    except ValueError as exc:
        raise ProjectError(f"Файл находится вне папки заданий: {path}") from exc
    if len(relative.parts) != 3:
        raise ProjectError(
            f"Ожидался путь Задания/Предмет/Тема/Файл.tex, получено: {relative}"
        )

    subject, theme, filename = relative.parts
    lines = _trim_empty_edges(path.read_text(encoding="utf-8-sig").splitlines())
    issues: list[Issue] = []
    answer: str | None = None
    difficulty: str | None = None

    answer_positions = [index for index, line in enumerate(lines) if ANSWER_RE.match(line)]
    if lines and (match := ANSWER_RE.match(lines[-1])):
        answer = match.group(1).strip()
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and (match := DIFFICULTY_RE.match(lines[-1])):
            difficulty = match.group(1).strip() or None
            lines.pop()
    elif answer_positions:
        issues.append(Issue("ошибка", path, "строка «% Ответ: ...» должна быть последней непустой строкой"))
    else:
        issues.append(Issue("предупреждение", path, "не указана последняя строка «% Ответ: ...»"))

    body = "\n".join(_trim_empty_edges(lines)).strip()
    if not body:
        issues.append(Issue("ошибка", path, "текст задания пуст"))
    if answer is not None and not answer:
        issues.append(Issue("предупреждение", path, "после «% Ответ:» ничего не указано"))

    task = Task(
        subject=subject,
        theme=theme,
        name=Path(filename).stem,
        path=path,
        body=body,
        answer=answer,
        difficulty=difficulty,
    )
    return task, issues


def scan_tasks(tasks_root: Path = TASKS_DIR) -> tuple[Catalog, list[Issue]]:
    catalog: Catalog = {}
    issues: list[Issue] = []
    if not tasks_root.exists():
        return {}, [Issue("ошибка", tasks_root, "папка «Задания» не найдена")]

    for path in sorted(tasks_root.rglob("*.tex"), key=lambda item: natural_key(str(item))):
        relative = path.relative_to(tasks_root)
        if any(part.startswith(".") for part in relative.parts) or path.name.startswith("_"):
            continue
        if len(relative.parts) != 3:
            issues.append(
                Issue("ошибка", path, "файл должен лежать строго в Задания/Предмет/Тема/Файл.tex")
            )
            continue
        try:
            task, task_issues = parse_task_file(path, tasks_root)
        except (OSError, UnicodeError, ProjectError) as exc:
            issues.append(Issue("ошибка", path, str(exc)))
            continue
        issues.extend(task_issues)
        catalog.setdefault(task.subject, {}).setdefault(task.theme, []).append(task)

    for themes in catalog.values():
        for tasks in themes.values():
            tasks.sort(key=lambda task: natural_key(task.name))
    if not catalog:
        issues.append(Issue("ошибка", tasks_root, "не найдено ни одного файла задания .tex"))
    return catalog, issues


def error_issues(issues: Iterable[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.level == "ошибка"]


def catalog_counts(catalog: Catalog) -> tuple[int, int, int]:
    subjects = len(catalog)
    themes = sum(len(subject_themes) for subject_themes in catalog.values())
    tasks = sum(len(items) for subject_themes in catalog.values() for items in subject_themes.values())
    return subjects, themes, tasks


def save_scheme(
    subject: str,
    name: str,
    theme_counts: dict[str, int],
    catalog: Catalog,
    schemes_root: Path = SCHEMES_DIR,
) -> Scheme:
    if subject not in catalog:
        raise ProjectError(f"Предмет «{subject}» не найден")
    cleaned: dict[str, int] = {}
    for theme, count in theme_counts.items():
        if theme not in catalog[subject]:
            raise ProjectError(f"Тема «{theme}» не найдена в предмете «{subject}»")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ProjectError(f"Для темы «{theme}» нужно указать целое неотрицательное число")
        available = len(catalog[subject][theme])
        if count > available:
            raise ProjectError(f"В теме «{theme}» только {available} заданий, запрошено {count}")
        if count:
            cleaned[theme] = count
    if not cleaned:
        raise ProjectError("В схеме должно быть хотя бы одно задание")

    scheme_name = name.strip() or "Новая схема"
    directory = schemes_root / safe_name(subject)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_name(scheme_name)}.json"
    data = {"version": 1, "name": scheme_name, "subject": subject, "themes": cleaned}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Scheme(scheme_name, subject, cleaned, path)


def load_scheme(path: Path) -> Scheme:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Не удалось прочитать схему {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError(f"Некорректная схема: {path}")
    name = data.get("name")
    subject = data.get("subject")
    themes = data.get("themes")
    if not isinstance(name, str) or not isinstance(subject, str) or not isinstance(themes, dict):
        raise ProjectError(f"В схеме должны быть строки name, subject и объект themes: {path}")
    normalized: dict[str, int] = {}
    for theme, count in themes.items():
        if not isinstance(theme, str) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ProjectError(f"Некорректное число заданий для темы «{theme}» в {path}")
        if count:
            normalized[theme] = count
    if not normalized:
        raise ProjectError(f"В схеме нет заданий: {path}")
    return Scheme(name, subject, normalized, path)


def list_schemes(schemes_root: Path = SCHEMES_DIR) -> list[Scheme]:
    if not schemes_root.exists():
        return []
    schemes: list[Scheme] = []
    for path in sorted(schemes_root.rglob("*.json"), key=lambda item: natural_key(str(item))):
        schemes.append(load_scheme(path))
    return schemes


def validate_scheme(scheme: Scheme, catalog: Catalog) -> None:
    if scheme.subject not in catalog:
        raise ProjectError(f"Предмет из схемы не найден: «{scheme.subject}»")
    for theme, count in scheme.themes.items():
        if theme not in catalog[scheme.subject]:
            raise ProjectError(f"Тема из схемы не найдена: «{theme}»")
        available = len(catalog[scheme.subject][theme])
        if count > available:
            raise ProjectError(f"В теме «{theme}» только {available} заданий, в схеме указано {count}")


def choose_variants(
    scheme: Scheme,
    catalog: Catalog,
    variants: int,
    seed: int | None = None,
) -> list[list[Task]]:
    if variants < 1:
        raise ProjectError("Количество вариантов должно быть не меньше 1")
    validate_scheme(scheme, catalog)
    rng = random.Random(seed)
    result: list[list[Task]] = []
    for _ in range(variants):
        selected: list[Task] = []
        for theme, count in scheme.themes.items():
            selected.extend(rng.sample(catalog[scheme.subject][theme], count))
        rng.shuffle(selected)
        result.append(selected)
    return result


def _preamble(title: str) -> str:
    return rf"""\documentclass[a4paper,11pt]{{article}}
\usepackage{{fontspec}}
\setmainfont{{Times New Roman}}
\usepackage[russian]{{babel}}
\usepackage{{amsmath,amssymb}}
\usepackage{{enumitem}}
\usepackage{{array}}
\usepackage{{geometry}}
\geometry{{left=20mm,right=18mm,top=18mm,bottom=20mm}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{4pt}}
\setlength{{\emergencystretch}}{{3em}}
\setlist[enumerate,1]{{label=\textbf{{\arabic*.}},leftmargin=2.2em,itemsep=1.0em,topsep=0.4em}}
\setlist[enumerate,2]{{label=\arabic*),leftmargin=2.1em,itemsep=0.15em,topsep=0.2em}}
\begin{{document}}
\begin{{center}}
{{\Large\bfseries {tex_escape(title)}}}
\end{{center}}
"""


def _render_task_item(task: Task) -> str:
    relative = f"{task.subject}/{task.theme}/{task.path.name}".replace("\\", "/")
    return f"% Источник: {relative}\n\\item {task.body}"


def render_answer_form(task_count: int, columns_per_block: int = 10) -> str:
    """Компактный бланк, который помещается в начало варианта."""
    if task_count < 1:
        return ""
    lines = [r"\begin{center}", r"\textbf{Бланк ответов}\\[0.4em]"]
    for first in range(1, task_count + 1, columns_per_block):
        numbers = list(range(first, min(first + columns_per_block, task_count + 1)))
        column_spec = "|c|" + r">{\centering\arraybackslash}p{0.78cm}|" * len(numbers)
        lines.extend(
            [
                r"\renewcommand{\arraystretch}{1.25}",
                rf"\begin{{tabular}}{{{column_spec}}}",
                r"\hline",
                "№ задания & " + " & ".join(str(number) for number in numbers) + r" \\ \hline",
                r"Ответ\rule{0pt}{0.85cm} & " + " & ".join("" for _ in numbers) + r" \\ \hline",
                r"\end{tabular}\\[0.55em]",
            ]
        )
    lines.append(r"\end{center}")
    return "\n".join(lines)


def render_subject_document(subject: str, themes: dict[str, list[Task]], include_answers: bool = True) -> str:
    lines = [_preamble(f"Все задания по предмету «{subject}»")]
    ordered_tasks: list[Task] = []
    number = 1
    for theme in sorted(themes, key=natural_key):
        tasks = themes[theme]
        lines.append(rf"\section*{{{tex_escape(theme)}}}")
        lines.append(rf"\begin{{enumerate}}[start={number}]")
        for task in tasks:
            lines.append(_render_task_item(task))
            ordered_tasks.append(task)
            number += 1
        lines.append(r"\end{enumerate}")
    if include_answers:
        lines.extend([r"\clearpage", r"\section*{Ответы}", r"\begin{enumerate}"])
        for task in ordered_tasks:
            answer = task.answer if task.answer else r"\textit{не указан}"
            lines.append(rf"\item {answer}")
        lines.append(r"\end{enumerate}")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def render_variant_document(subject: str, scheme_name: str, variant_number: int, tasks: Sequence[Task]) -> str:
    lines = [
        _preamble("Контрольная работа"),
        rf"\textbf{{Предмет:}} {tex_escape(subject)}\\",
        rf"\textbf{{Схема:}} {tex_escape(scheme_name)}\\",
        rf"\textbf{{Вариант:}} {variant_number}\\[0.5em]",
        r"ФИО: \rule{0.68\textwidth}{0.4pt}\\[0.5em]",
        r"Группа: \rule{0.25\textwidth}{0.4pt}\hfill Дата: \rule{0.25\textwidth}{0.4pt}",
        r"\vspace{0.8em}",
        render_answer_form(len(tasks)),
        r"\vspace{0.4em}",
        r"\begin{enumerate}",
    ]
    lines.extend(_render_task_item(task) for task in tasks)
    lines.extend([r"\end{enumerate}", r"\end{document}"])
    return "\n".join(lines) + "\n"


def render_answers_document(subject: str, scheme_name: str, variants: Sequence[Sequence[Task]]) -> str:
    lines = [_preamble("Ключ ответов"), rf"\textbf{{Предмет:}} {tex_escape(subject)}\\", rf"\textbf{{Схема:}} {tex_escape(scheme_name)}"]
    for variant_number, tasks in enumerate(variants, 1):
        lines.extend([rf"\section*{{Вариант {variant_number}}}", r"\begin{enumerate}"])
        for task in tasks:
            answer = task.answer if task.answer else r"\textit{не указан}"
            lines.append(rf"\item {answer}")
        lines.append(r"\end{enumerate}")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def _new_output_directory(results_root: Path, subject: str, label: str) -> Path:
    subject_dir = results_root / safe_name(subject)
    subject_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{safe_name(label)}"
    directory = subject_dir / stem
    counter = 2
    while directory.exists():
        directory = subject_dir / f"{stem}_{counter}"
        counter += 1
    directory.mkdir()
    return directory


def export_subject(
    subject: str,
    catalog: Catalog,
    results_root: Path = RESULTS_DIR,
    include_answers: bool = True,
) -> OutputBundle:
    if subject not in catalog:
        raise ProjectError(f"Предмет «{subject}» не найден")
    directory = _new_output_directory(results_root, subject, "Все задания")
    tex_path = directory / f"{safe_name(subject)}_все_задания.tex"
    tex_path.write_text(render_subject_document(subject, catalog[subject], include_answers), encoding="utf-8")
    return OutputBundle(directory, (tex_path,), ())


def generate_variants(
    scheme: Scheme,
    catalog: Catalog,
    variants_count: int,
    results_root: Path = RESULTS_DIR,
    seed: int | None = None,
) -> OutputBundle:
    variants = choose_variants(scheme, catalog, variants_count, seed)
    directory = _new_output_directory(results_root, scheme.subject, scheme.name)
    tex_files: list[Path] = []
    for number, tasks in enumerate(variants, 1):
        path = directory / f"Вариант_{number:02d}.tex"
        path.write_text(render_variant_document(scheme.subject, scheme.name, number, tasks), encoding="utf-8")
        tex_files.append(path)

    answers_tex = directory / "Ответы.tex"
    answers_tex.write_text(render_answers_document(scheme.subject, scheme.name, variants), encoding="utf-8")

    answers_csv = directory / "Ответы.csv"
    with answers_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Номер задания", *[f"Вариант {number}" for number in range(1, variants_count + 1)]])
        max_tasks = max(len(tasks) for tasks in variants)
        for index in range(max_tasks):
            writer.writerow([index + 1, *[(tasks[index].answer or "") for tasks in variants]])

    composition_csv = directory / "Состав_вариантов.csv"
    with composition_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Вариант", "Номер задания", "Тема", "Файл"])
        for variant_number, tasks in enumerate(variants, 1):
            for task_number, task in enumerate(tasks, 1):
                writer.writerow([variant_number, task_number, task.theme, task.path.name])

    return OutputBundle(directory, tuple(tex_files + [answers_tex]), (answers_csv, composition_csv))


def find_xelatex() -> str | None:
    return shutil.which("xelatex")


def compile_tex(path: Path, executable: str | None = None) -> Path:
    command = executable or find_xelatex()
    if not command:
        raise ProjectError("XeLaTeX не найден. Файл .tex создан, PDF можно собрать позже.")
    output = ""
    for _ in range(2):
        try:
            completed = subprocess.run(
                [command, "-interaction=nonstopmode", "-halt-on-error", path.name],
                cwd=path.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError(f"Не удалось собрать PDF из {path.name}: {exc}") from exc
        output = completed.stdout
        if completed.returncode != 0:
            tail = "\n".join(output.splitlines()[-25:])
            raise ProjectError(f"XeLaTeX сообщил об ошибке в {path.name}:\n{tail}")
    pdf = path.with_suffix(".pdf")
    if not pdf.exists():
        raise ProjectError(f"После сборки не найден PDF: {pdf}")
    for suffix in (".aux", ".out", ".toc", ".log"):
        auxiliary = path.with_suffix(suffix)
        if auxiliary.exists():
            auxiliary.unlink()
    return pdf
