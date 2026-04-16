import generated.docubrain_openapi_client.docubrain_openapi_client as docubrain_api  # type: ignore[import-untyped,unused-ignore]
from tests.integration.common_utils.constants import API_SERVER_URL

api_config = docubrain_api.Configuration(host=API_SERVER_URL)
