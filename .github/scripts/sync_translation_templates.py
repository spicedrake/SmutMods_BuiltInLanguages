#!/usr/bin/env python3

"""
Synchronizes all non-English built-in translation CSV files against
the canonical English product CSV files.

For each product CSV:

- Existing translation rows are matched by Key.
- Existing Translation values are preserved.
- Existing Notes values are preserved.
- English reference text is updated from the canonical English CSV.
- Missing keys are inserted with empty Translation and Notes columns.
- Rows follow the same order as the English source.
- Translation-only legacy rows are preserved at the end.
- Duplicate keys and malformed headers cause a hard failure.

This script processes:

- SmutMods.csv
- SmutShared.csv
- SmutLabs.csv

inside every LanguagePacks directory except LanguagePacks/English.
"""

from __future__ import annotations

import csv
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LANGUAGE_PACKS_ROOT = REPOSITORY_ROOT / "LanguagePacks"
ENGLISH_DIRECTORY = LANGUAGE_PACKS_ROOT / "English"

PRODUCT_FILES = (
    "SmutMods.csv",
    "SmutShared.csv",
    "SmutLabs.csv",
)

EXPECTED_HEADER = (
    "Key",
    "English",
    "Translation",
    "Notes",
)


@dataclass(frozen=True)
class CsvRow:
    key: str
    english: str
    translation: str
    notes: str


def main() -> int:
    validate_repository_structure()

    language_directories = find_non_english_language_directories()

    if not language_directories:
        print("No non-English Language Pack directories were found.")
        return 0

    changed_files = 0

    for product_file_name in PRODUCT_FILES:
        english_path = ENGLISH_DIRECTORY / product_file_name
        english_rows = read_csv(english_path)

        print()
        print(f"Synchronizing {product_file_name}")
        print(f"Canonical English rows: {len(english_rows)}")

        for language_directory in language_directories:
            translation_path = language_directory / product_file_name

            changed = synchronize_file(
                english_rows=english_rows,
                translation_path=translation_path,
            )

            relative_path = translation_path.relative_to(REPOSITORY_ROOT)

            if changed:
                changed_files += 1
                print(f"  updated: {relative_path}")
            else:
                print(f"  current: {relative_path}")

    print()
    print(f"Synchronization complete. Changed files: {changed_files}")

    return 0


def validate_repository_structure() -> None:
    if not LANGUAGE_PACKS_ROOT.is_dir():
        fail(f"Missing LanguagePacks directory: {LANGUAGE_PACKS_ROOT}")

    if not ENGLISH_DIRECTORY.is_dir():
        fail(f"Missing English Language Pack directory: {ENGLISH_DIRECTORY}")

    for product_file_name in PRODUCT_FILES:
        english_path = ENGLISH_DIRECTORY / product_file_name

        if not english_path.is_file():
            fail(f"Missing canonical English CSV: {english_path}")


def find_non_english_language_directories() -> list[Path]:
    directories: list[Path] = []

    for child in sorted(LANGUAGE_PACKS_ROOT.iterdir()):
        if not child.is_dir():
            continue

        if child.name.casefold() == "english":
            continue

        # A Language Pack directory must contain a manifest.
        if not (child / "pack.json").is_file():
            print(
                f"Skipping directory without pack.json: "
                f"{child.relative_to(REPOSITORY_ROOT)}"
            )
            continue

        directories.append(child)

    return directories


def synchronize_file(
    english_rows: list[CsvRow],
    translation_path: Path,
) -> bool:
    existing_rows = (
        read_csv(translation_path)
        if translation_path.is_file()
        else []
    )

    existing_by_key = index_rows(
        existing_rows,
        translation_path,
    )

    synchronized_rows: list[CsvRow] = []
    english_keys: set[str] = set()

    for english_row in english_rows:
        english_keys.add(english_row.key)

        existing = existing_by_key.get(english_row.key)

        if existing is None:
            synchronized_rows.append(
                CsvRow(
                    key=english_row.key,
                    english=english_row.english,
                    translation="",
                    notes="",
                )
            )
            continue

        synchronized_rows.append(
            CsvRow(
                key=english_row.key,
                english=english_row.english,
                translation=existing.translation,
                notes=existing.notes,
            )
        )

    # Preserve translation-only rows that are no longer present in English.
    # They are placed after all current canonical rows so no translator work
    # is silently destroyed.
    for existing_row in existing_rows:
        if existing_row.key in english_keys:
            continue

        synchronized_rows.append(existing_row)

    new_content = serialize_csv(synchronized_rows)

    old_content = (
        normalize_newlines(
            translation_path.read_text(encoding="utf-8-sig")
        )
        if translation_path.is_file()
        else ""
    )

    if old_content == new_content:
        return False

    translation_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    translation_path.write_text(
        new_content,
        encoding="utf-8",
        newline="",
    )

    return True


def read_csv(path: Path) -> list[CsvRow]:
    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            reader = csv.reader(stream)
            raw_rows = list(reader)
    except OSError as exception:
        fail(f"Could not read CSV '{path}': {exception}")

    if not raw_rows:
        fail(f"CSV is empty: {path}")

    header = tuple(raw_rows[0])

    if header != EXPECTED_HEADER:
        fail(
            f"Unexpected CSV header in '{path}'.\n"
            f"Expected: {EXPECTED_HEADER}\n"
            f"Actual:   {header}"
        )

    rows: list[CsvRow] = []
    seen_keys: set[str] = set()

    for row_number, raw_row in enumerate(
        raw_rows[1:],
        start=2,
    ):
        if len(raw_row) != 4:
            fail(
                f"Malformed CSV row in '{path}' at line {row_number}. "
                f"Expected 4 columns but found {len(raw_row)}.\n"
                f"Row: {raw_row}"
            )

        key = raw_row[0].strip()

        if not key:
            fail(
                f"Empty localization key in '{path}' "
                f"at line {row_number}."
            )

        if key in seen_keys:
            fail(
                f"Duplicate localization key '{key}' "
                f"in '{path}' at line {row_number}."
            )

        seen_keys.add(key)

        rows.append(
            CsvRow(
                key=key,
                english=raw_row[1],
                translation=raw_row[2],
                notes=raw_row[3],
            )
        )

    return rows


def index_rows(
    rows: Iterable[CsvRow],
    path: Path,
) -> dict[str, CsvRow]:
    indexed: dict[str, CsvRow] = {}

    for row in rows:
        if row.key in indexed:
            fail(
                f"Duplicate localization key '{row.key}' "
                f"in '{path}'."
            )

        indexed[row.key] = row

    return indexed


def serialize_csv(rows: Iterable[CsvRow]) -> str:
    output = io.StringIO(newline="")

    writer = csv.writer(
        output,
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )

    writer.writerow(EXPECTED_HEADER)

    for row in rows:
        writer.writerow(
            (
                row.key,
                row.english,
                row.translation,
                row.notes,
            )
        )

    return output.getvalue()


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())