"""
Pydantic models for structured agent outputs.

These models define the schema for agent responses, enabling:
- Type-safe responses
- Automatic validation
- Better IDE support
- Consistent response formats
"""


from pydantic import BaseModel, Field


class SheetInfo(BaseModel):
    """Information about a Smartsheet."""
    id: int = Field(..., description="The sheet ID")
    name: str = Field(..., description="The sheet name")
    row_count: int = Field(default=0, description="Number of rows in the sheet")
    column_count: int = Field(default=0, description="Number of columns in the sheet")
    access_level: str = Field(default="VIEWER", description="User's access level")


class SheetSummary(BaseModel):
    """Summary statistics for a Smartsheet."""
    sheet_name: str = Field(..., description="Name of the sheet")
    total_rows: int = Field(..., description="Total number of rows")
    total_columns: int = Field(..., description="Total number of columns")
    column_names: list[str] = Field(default_factory=list, description="List of column names")
    fill_rates: dict[str, float] = Field(default_factory=dict, description="Fill rates by column")


class SearchResult(BaseModel):
    """A search result from Smartsheet."""
    sheet_name: str = Field(..., description="Name of the sheet containing the result")
    sheet_id: int = Field(..., description="ID of the sheet")
    row_number: int = Field(..., description="Row number where match was found")
    matched_text: str = Field(..., description="The text that matched the search")
    context: str | None = Field(None, description="Surrounding context")


class RowData(BaseModel):
    """Data from a specific row."""
    row_id: int = Field(..., description="The row ID")
    row_number: int = Field(..., description="The row number (1-indexed)")
    cells: dict[str, str] = Field(default_factory=dict, description="Cell values by column name")


class StatusBreakdown(BaseModel):
    """Breakdown of values in a column (e.g., status counts)."""
    column_name: str = Field(..., description="Name of the column analyzed")
    total_rows: int = Field(..., description="Total rows analyzed")
    breakdown: dict[str, int] = Field(default_factory=dict, description="Count by value")


class AgentResponse(BaseModel):
    """Standard response from the Smartsheet Agent."""
    success: bool = Field(..., description="Whether the query was successful")
    message: str = Field(..., description="Human-readable response message")
    data_type: str | None = Field(None, description="Type of data returned (sheet, search, summary, etc.)")
    sheets: list[SheetInfo] | None = Field(None, description="List of sheets if applicable")
    summary: SheetSummary | None = Field(None, description="Sheet summary if applicable")
    search_results: list[SearchResult] | None = Field(None, description="Search results if applicable")
    rows: list[RowData] | None = Field(None, description="Row data if applicable")
    status_breakdown: StatusBreakdown | None = Field(None, description="Status breakdown if applicable")
    error: str | None = Field(None, description="Error message if unsuccessful")
