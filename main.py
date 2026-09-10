from __future__ import annotations

import argparse
import sys
from pathlib import Path

from umf_latex import (
    PROJECT_DIR,
    SCHEMES_DIR,
    Catalog,
    ProjectError,
    Scheme,
    catalog_counts,
    compile_tex,
    error_issues,
    export_subject,
    find_xelatex,
    generate_variants,
    list_schemes,
    load_scheme,
    natural_key,
    save_scheme,
    scan_tasks,
)


def choose_from_list(title: str, values: list[str]) -> str:
    if not values:
        raise ProjectError("Нет доступных вариантов выбора")
    print(f"\n{title}")
    for index, value in enumerate(values, 1):
        print(f"  {index}. {value}")
    while True:
        raw = input("Введите номер: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(values):
            return values[int(raw) - 1]
        print("Введите номер из списка.")


def yes_no(prompt: str, default: bool = False) -> bool:
    answer = input(prompt + " [д/н]: ").strip().casefold()
    if not answer:
        return default
    return answer in {"д", "да", "y", "yes"}


def show_issues(catalog: Catalog, issues: list) -> bool:
    subjects, themes, tasks = catalog_counts(catalog)
    print(f"\nНайдено: предметов — {subjects}, тем — {themes}, заданий — {tasks}.")
    if not issues:
        print("Ошибок и предупреждений нет.")
        return True
    print("\nРезультат проверки:")
    for issue in issues:
        try:
            shown_path = issue.path.relative_to(PROJECT_DIR)
        except ValueError:
            shown_path = issue.path
        print(f"  [{issue.level.upper()}] {shown_path}: {issue.message}")
    return not error_issues(issues)


def require_catalog() -> Catalog:
    catalog, issues = scan_tasks()
    if not show_issues(catalog, issues):
        raise ProjectError("Сначала исправьте ошибки структуры, показанные выше")
    return catalog


def interactive_export() -> None:
    catalog = require_catalog()
    subject = choose_from_list("Выберите предмет", sorted(catalog, key=natural_key))
    bundle = export_subject(subject, catalog, include_answers=True)
    print(f"\nГотово: {bundle.tex_files[0]}")
    if find_xelatex() and yes_no("Собрать также PDF?"):
        print(f"PDF: {compile_tex(bundle.tex_files[0])}")


def parse_numbered_counts(raw: str, themes: list[str], catalog: Catalog, subject: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ProjectError(f"Не найден знак = в «{part}»")
        left, right = (value.strip() for value in part.split("=", 1))
        if not left.isdigit() or not right.isdigit():
            raise ProjectError(f"Ожидался формат номер=количество, получено «{part}»")
        index = int(left)
        count = int(right)
        if not 1 <= index <= len(themes):
            raise ProjectError(f"Темы с номером {index} нет")
        theme = themes[index - 1]
        if theme in result:
            raise ProjectError(f"Тема {index} указана дважды")
        available = len(catalog[subject][theme])
        if count > available:
            raise ProjectError(f"В теме {index} только {available} заданий")
        if count:
            result[theme] = count
    if not result:
        raise ProjectError("Не выбрано ни одного задания")
    return result


def interactive_scheme() -> None:
    catalog = require_catalog()
    subject = choose_from_list("Выберите предмет", sorted(catalog, key=natural_key))
    themes = sorted(catalog[subject], key=natural_key)
    print("\nТемы и количество доступных заданий:")
    for index, theme in enumerate(themes, 1):
        print(f"  {index}. {theme} — {len(catalog[subject][theme])}")
    print("\nВведите состав одной строкой. Пример: 1=3, 2=5, 3=2")
    while True:
        try:
            counts = parse_numbered_counts(input("Состав: ").strip(), themes, catalog, subject)
            break
        except ProjectError as exc:
            print(f"Ошибка: {exc}")
    name = input("Название схемы (например, КР 1): ").strip() or "Новая схема"
    scheme = save_scheme(subject, name, counts, catalog)
    total = sum(scheme.themes.values())
    print(f"\nСхема сохранена: {scheme.path}")
    print(f"Заданий в одном варианте: {total}")


def interactive_generate() -> None:
    catalog = require_catalog()
    schemes = list_schemes()
    if not schemes:
        raise ProjectError("Схем пока нет. Сначала выберите пункт 2 и создайте схему.")
    labels = [f"{scheme.name} — {scheme.subject} ({sum(scheme.themes.values())} заданий)" for scheme in schemes]
    selected = choose_from_list("Выберите схему", labels)
    scheme = schemes[labels.index(selected)]
    while True:
        raw = input("Сколько вариантов создать: ").strip()
        if raw.isdigit() and int(raw) > 0:
            count = int(raw)
            break
        print("Введите целое число больше нуля.")
    bundle = generate_variants(scheme, catalog, count)
    print(f"\nГотово. Результаты находятся в:\n{bundle.directory}")
    if find_xelatex() and yes_no("Собрать PDF для всех вариантов и ключа ответов?"):
        for tex_path in bundle.tex_files:
            print(f"  {compile_tex(tex_path).name}")


def interactive_validate() -> None:
    catalog, issues = scan_tasks()
    show_issues(catalog, issues)


def interactive_menu() -> None:
    actions = {
        "1": interactive_export,
        "2": interactive_scheme,
        "3": interactive_generate,
        "4": interactive_validate,
    }
    while True:
        print(
            "\nГенератор контрольных работ\n"
            "  1. Выгрузить все задания по предмету\n"
            "  2. Создать схему контрольной работы\n"
            "  3. Создать варианты по схеме\n"
            "  4. Проверить базу заданий\n"
            "  0. Выход"
        )
        choice = input("Выберите пункт: ").strip()
        if choice == "0":
            return
        action = actions.get(choice)
        if not action:
            print("Такого пункта нет.")
            continue
        try:
            action()
        except (ProjectError, OSError) as exc:
            print(f"\nОшибка: {exc}")


def parse_theme_arguments(values: list[str] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values or []:
        if "=" not in value:
            raise ProjectError(f"Ожидался формат «Название темы=число», получено: {value}")
        theme, raw_count = value.rsplit("=", 1)
        if not raw_count.strip().isdigit():
            raise ProjectError(f"Некорректное количество в: {value}")
        result[theme.strip()] = int(raw_count.strip())
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Генератор контрольных работ из файлов LaTeX")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("validate", help="проверить структуру заданий")

    export_parser = subparsers.add_parser("export", help="выгрузить предмет в один файл")
    export_parser.add_argument("--subject", required=True, help="название предмета")
    export_parser.add_argument("--no-answers", action="store_true", help="не добавлять ответы в конец")
    export_parser.add_argument("--compile", action="store_true", help="собрать PDF через XeLaTeX")

    scheme_parser = subparsers.add_parser("scheme", help="создать схему")
    scheme_parser.add_argument("--subject", required=True)
    scheme_parser.add_argument("--name", required=True)
    scheme_parser.add_argument("--theme", action="append", help="Название темы=количество", required=True)

    generate_parser = subparsers.add_parser("generate", help="создать варианты")
    generate_parser.add_argument("--scheme", type=Path, required=True)
    generate_parser.add_argument("--variants", type=int, required=True)
    generate_parser.add_argument("--seed", type=int)
    generate_parser.add_argument("--compile", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.command == "validate":
        catalog, issues = scan_tasks()
        return 0 if show_issues(catalog, issues) else 1

    catalog = require_catalog()
    if args.command == "export":
        bundle = export_subject(args.subject, catalog, include_answers=not args.no_answers)
        print(bundle.tex_files[0])
        if args.compile:
            print(compile_tex(bundle.tex_files[0]))
    elif args.command == "scheme":
        scheme = save_scheme(args.subject, args.name, parse_theme_arguments(args.theme), catalog)
        print(scheme.path)
    elif args.command == "generate":
        scheme_path = args.scheme if args.scheme.is_absolute() else PROJECT_DIR / args.scheme
        bundle = generate_variants(load_scheme(scheme_path), catalog, args.variants, seed=args.seed)
        print(bundle.directory)
        if args.compile:
            for tex_path in bundle.tex_files:
                print(compile_tex(tex_path))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except OSError:
            pass
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        interactive_menu()
        return 0
    try:
        return run_cli(args)
    except (ProjectError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
