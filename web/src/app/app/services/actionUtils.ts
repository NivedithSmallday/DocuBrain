import { JSX } from "react";
import type { IconProps } from "@opal/types";
import { ToolSnapshot } from "@/lib/tools/interfaces";
import { SvgCpu, SvgLink, SvgSearch, SvgServer } from "@opal/icons";

// Helper functions to identify specific tools
const isSearchTool = (tool: ToolSnapshot): boolean => {
  return (
    tool.in_code_tool_id === "SearchTool" ||
    tool.name === "run_search" ||
    tool.display_name?.toLowerCase().includes("search tool")
  );
};

const isKnowledgeGraphTool = (tool: ToolSnapshot): boolean => {
  return (
    tool.in_code_tool_id === "KnowledgeGraphTool" ||
    tool.display_name?.toLowerCase().includes("knowledge graph")
  );
};

const isOpenUrlTool = (tool: ToolSnapshot): boolean => {
  return (
    tool.in_code_tool_id === "OpenURLTool" ||
    tool.name === "open_url" ||
    tool.display_name?.toLowerCase().includes("open url")
  );
};

export function getIconForAction(
  action: ToolSnapshot
): (props: IconProps) => JSX.Element {
  if (isSearchTool(action)) return SvgSearch;
  if (isKnowledgeGraphTool(action)) return SvgServer;
  if (isOpenUrlTool(action)) return SvgLink;
  return SvgCpu;
}

// Check if the agent has internal search available.
export function hasSearchToolsAvailable(tools: ToolSnapshot[]): boolean {
  return tools.some((tool) => isSearchTool(tool));
}
