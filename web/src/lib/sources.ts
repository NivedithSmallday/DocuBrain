import {
  GithubIcon,
  GmailIcon,
  GoogleDriveIcon,
} from "@/components/icons/icons";
import { Persona } from "@/app/admin/agents/interfaces";
import { ValidSources } from "./types";
import { SourceCategory, SourceMetadata } from "./search/interfaces";
import React from "react";
import { DOCS_ADMINS_PATH } from "./constants";
import { SvgFileText } from "@opal/icons";

interface PartialSourceMetadata {
  icon: React.FC<{ size?: number; className?: string }>;
  displayName: string;
  category: SourceCategory;
  isPopular?: boolean;
  docs?: string;
  oauthSupported?: boolean;
  alwaysConnected?: boolean;
  customDescription?: string;
}

type SourceMap = {
  [K in ValidSources]?: PartialSourceMetadata;
};

export const SOURCE_METADATA_MAP: SourceMap = {
  github: {
    icon: GithubIcon,
    displayName: "GitHub",
    category: SourceCategory.CodeRepository,
    docs: `${DOCS_ADMINS_PATH}/connectors/official/github`,
    isPopular: true,
  },
  google_drive: {
    icon: GoogleDriveIcon,
    displayName: "Google Drive",
    category: SourceCategory.Storage,
    docs: `${DOCS_ADMINS_PATH}/connectors/official/google_drive/overview`,
    oauthSupported: true,
    isPopular: true,
  },
  gmail: {
    icon: GmailIcon,
    displayName: "Gmail",
    category: SourceCategory.Messaging,
    docs: `${DOCS_ADMINS_PATH}/connectors/official/gmail`,
    oauthSupported: true,
    isPopular: true,
  },
  user_file: {
    icon: SvgFileText,
    displayName: "Uploaded Files",
    category: SourceCategory.Other,
    alwaysConnected: true,
    customDescription: "Manage files uploaded in chats and projects",
  },
  craft_file: {
    icon: SvgFileText,
    displayName: "Craft Files",
    category: SourceCategory.Other,
    alwaysConnected: true,
    customDescription: "Files available inside the build workspace",
  },
  ingestion_api: {
    icon: SvgFileText,
    displayName: "Ingestion API",
    category: SourceCategory.Other,
  },
  not_applicable: {
    icon: SvgFileText,
    displayName: "Other",
    category: SourceCategory.Other,
  },
};

function fillSourceMetadata(
  partialMetadata: PartialSourceMetadata,
  internalName: ValidSources
): SourceMetadata {
  return {
    ...partialMetadata,
    internalName,
    adminUrl: `/admin/connectors/${internalName}`,
  };
}

export function getSourceMetadata(sourceType: ValidSources): SourceMetadata {
  const partialMetadata =
    SOURCE_METADATA_MAP[sourceType] ||
    SOURCE_METADATA_MAP[ValidSources.NotApplicable];

  return fillSourceMetadata(
    partialMetadata as PartialSourceMetadata,
    partialMetadata ? sourceType : ValidSources.NotApplicable
  );
}

export function listSourceMetadata(): SourceMetadata[] {
  return [
    ValidSources.GitHub,
    ValidSources.GoogleDrive,
    ValidSources.Gmail,
  ].map((source) =>
    getSourceMetadata(source)
  );
}

export function getSourceDocLink(sourceType: ValidSources): string | null {
  return SOURCE_METADATA_MAP[sourceType]?.docs || null;
}

export const isValidSource = (sourceType: string) => {
  return Object.keys(SOURCE_METADATA_MAP).includes(sourceType);
};

export function getSourceDisplayName(sourceType: ValidSources): string | null {
  return getSourceMetadata(sourceType).displayName;
}

export function getSourceMetadataForSources(sources: ValidSources[]) {
  return sources.map((source) => getSourceMetadata(source));
}

export function getSourcesForPersona(persona: Persona): ValidSources[] {
  const personaSources: ValidSources[] = [];
  persona.document_sets.forEach((documentSet) => {
    documentSet.cc_pair_summaries.forEach((ccPair) => {
      if (!personaSources.includes(ccPair.source)) {
        personaSources.push(ccPair.source);
      }
    });
  });
  return personaSources;
}

export async function fetchTitleFromUrl(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      method: "GET",
      mode: "cors",
    });
    if (!response.ok) {
      return null;
    }
    const html = await response.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    return doc.querySelector("title")?.innerText.trim() ?? null;
  } catch (error) {
    console.error("Error fetching page title:", error);
    return null;
  }
}
