"""Registry mapping for the supported connector classes."""

from pydantic import BaseModel

from docubrain.configs.constants import DocumentSource


class ConnectorMapping(BaseModel):
    module_path: str
    class_name: str


CONNECTOR_CLASS_MAP = {
    DocumentSource.GITHUB: ConnectorMapping(
        module_path="docubrain.connectors.github.connector",
        class_name="GithubConnector",
    ),
    DocumentSource.GOOGLE_DRIVE: ConnectorMapping(
        module_path="docubrain.connectors.google_drive.connector",
        class_name="GoogleDriveConnector",
    ),
}
