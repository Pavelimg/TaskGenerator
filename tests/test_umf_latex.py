from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from umf_latex import (
    Scheme,
    choose_variants,
    export_subject,
    generate_variants,
    parse_task_file,
    render_variant_document,
    save_scheme,
    scan_tasks,
)


class UmfLatexTests(unittest.TestCase):
    def make_task(self, root: Path, subject: str, theme: str, name: str, answer: str | None = "1") -> Path:
        path = root / subject / theme / f"{name}.tex"
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = f"\n\n% Сложность: 2\n% Ответ: {answer}\n" if answer is not None else "\n"
        path.write_text(r"Решите \(x+1=2\)." + suffix, encoding="utf-8")
        return path

    def test_parse_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Задания"
            path = self.make_task(root, "Алгебра", "Уравнения", "Задание 1", "1, 3")
            task, issues = parse_task_file(path, root)
            self.assertEqual(task.answer, "1, 3")
            self.assertEqual(task.difficulty, "2")
            self.assertNotIn("% Ответ", task.body)
            self.assertEqual(issues, [])

    def test_scan_warns_about_missing_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Задания"
            self.make_task(root, "Алгебра", "Уравнения", "Задание 1", None)
            catalog, issues = scan_tasks(root)
            self.assertEqual(len(catalog["Алгебра"]["Уравнения"]), 1)
            self.assertTrue(any(issue.level == "предупреждение" for issue in issues))

    def test_scheme_and_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "Задания"
            for index in range(4):
                self.make_task(root, "Алгебра", "Уравнения", f"Задание {index + 1}", str(index + 1))
            catalog, issues = scan_tasks(root)
            self.assertFalse([issue for issue in issues if issue.level == "ошибка"])
            scheme = save_scheme(
                "Алгебра", "КР 1", {"Уравнения": 3}, catalog, base / "Схемы"
            )
            variants = choose_variants(scheme, catalog, 5, seed=42)
            self.assertEqual(len(variants), 5)
            self.assertTrue(all(len(set(task.path for task in variant)) == 3 for variant in variants))

    def test_export_and_generation_write_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "Задания"
            for index in range(3):
                self.make_task(root, "Алгебра", "Уравнения", f"Задание {index + 1}", "1")
            catalog, _ = scan_tasks(root)
            exported = export_subject("Алгебра", catalog, base / "Результаты")
            self.assertTrue(exported.tex_files[0].exists())
            self.assertIn("Все задания", exported.tex_files[0].read_text(encoding="utf-8"))

            scheme = Scheme("КР", "Алгебра", {"Уравнения": 2}, base / "scheme.json")
            generated = generate_variants(scheme, catalog, 2, base / "Результаты", seed=7)
            self.assertEqual(len(generated.tex_files), 3)
            self.assertTrue(all(path.exists() for path in generated.tex_files + generated.other_files))

    def test_answer_form_is_at_the_beginning_of_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Задания"
            paths = [self.make_task(root, "Алгебра", "Уравнения", f"Задание {index}") for index in range(1, 13)]
            tasks = [parse_task_file(path, root)[0] for path in paths]
            document = render_variant_document("Алгебра", "КР", 1, tasks)
            self.assertIn("Бланк ответов", document)
            self.assertLess(document.index("Бланк ответов"), document.index(r"\begin{enumerate}", document.index("Бланк ответов")))
            self.assertIn("№ задания & 1 & 2 & 3", document)
            self.assertIn("№ задания & 11 & 12", document)


if __name__ == "__main__":
    unittest.main()
