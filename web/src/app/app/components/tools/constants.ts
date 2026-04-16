import type { IconProps } from "@opal/types";
import { SvgCode, SvgLink, SvgSearch } from "@opal/icons";

// Tool names as referenced by tool results / tool calls
export const SEARCH_TOOL_NAME = "run_search";
export const PYTHON_TOOL_NAME = "run_python";
export const OPEN_URL_TOOL_NAME = "open_url";

// In-code tool IDs that also correspond to the tool's name when associated with a persona
export const SEARCH_TOOL_ID = "SearchTool";
export const PYTHON_TOOL_ID = "PythonTool";
export const OPEN_URL_TOOL_ID = "OpenURLTool";
export const FILE_READER_TOOL_ID = "FileReaderTool";

// Icon mappings for system tools
export const SYSTEM_TOOL_ICONS: Record<
  string,
  React.FunctionComponent<IconProps>
> = {
  [SEARCH_TOOL_ID]: SvgSearch,
  [PYTHON_TOOL_ID]: SvgCode,
  [OPEN_URL_TOOL_ID]: SvgLink,
};
