import io
from typing import cast
from unittest.mock import patch

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from docubrain.file_processing.extract_file_text import xlsx_to_text


def _make_xlsx(sheets: dict[str, list[list[str]]]) -> io.BytesIO:
    """Create an in-memory xlsx file from a dict of sheet_name -> matrix of strings."""
    wb = openpyxl.Workbook()
    if wb.active is not None:
        wb.remove(cast(Worksheet, wb.active))
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class TestXlsxToText:
    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_single_sheet_basic(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [
                    ["Name", "Age"],
                    ["Alice", "30"],
                    ["Bob", "25"],
                ]
            }
        )
        result = xlsx_to_text(xlsx)
        lines = [line for line in result.strip().split("\n") if line.strip()]
        assert len(lines) == 4  # Includes header separation
        assert "| Name | Age |" in lines[0]
        assert "|---|---|" in lines[1]
        assert "| Alice | 30 |" in lines[2]
        assert "| Bob | 25 |" in lines[3]

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_multiple_sheets_separated(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [["a", "b"]],
                "Sheet2": [["c", "d"]],
            }
        )
        result = xlsx_to_text(xlsx)
        assert "\n\n" in result
        parts = result.split("\n\n")
        assert "| a | b |" in parts[0]
        assert "| c | d |" in parts[1]

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_empty_cells(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [
                    ["a", "", "b"],
                    ["", "c", ""],
                ]
            }
        )
        result = xlsx_to_text(xlsx)
        lines = [line for line in result.strip().split("\n") if line.strip()]
        assert len(lines) == 3
        # First row is header, second is separator, third is data
        assert "| a |  | b |" in lines[0]
        assert "|---|---|---|" in lines[1]
        assert "|  | c |  |" in lines[2]

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_commas_in_cells_not_an_issue_for_markdown(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [
                    ["hello, world", "normal"],
                ]
            }
        )
        result = xlsx_to_text(xlsx)
        assert "| hello, world | normal |" in result

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_empty_workbook_or_sheet(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx({"Sheet1": []})
        result = xlsx_to_text(xlsx)
        assert result.strip() == ""

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_bad_zip_file_returns_empty(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        bad_file = io.BytesIO(b"not a zip file")
        result = xlsx_to_text(bad_file, file_name="test.xlsx")
        assert result == ""

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_bad_zip_tilde_file_returns_empty(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        bad_file = io.BytesIO(b"not a zip file")
        result = xlsx_to_text(bad_file, file_name="~$temp.xlsx")
        assert result == ""

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_uneven_rows_are_padded(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [
                    ["h1", "h2", "h3"],
                    ["v1"],
                    ["v2", "v3"]
                ]
            }
        )
        result = xlsx_to_text(xlsx)
        lines = [line for line in result.strip().split("\n") if line.strip()]
        assert len(lines) == 4
        assert "| h1 | h2 | h3 |" in lines[0]
        assert "|---|---|---|" in lines[1]
        assert "| v1 |  |  |" in lines[2]
        assert "| v2 | v3 |  |" in lines[3]

    @patch("docubrain.file_processing.extract_file_text.get_markitdown_converter")
    def test_special_characters_escaped(self, mock_md: Any) -> None:
        mock_md.side_effect = Exception("Mocked failure")
        xlsx = _make_xlsx(
            {
                "Sheet1": [
                    ["h1", "h2"],
                    ["newline\nhere", "pipe|here"]
                ]
            }
        )
        result = xlsx_to_text(xlsx)
        lines = [line for line in result.strip().split("\n") if line.strip()]
        # newline removed via space, pipe escaped with \ or just space in replacement
        assert "| newline here | pipe\\|here |" in lines[2]
