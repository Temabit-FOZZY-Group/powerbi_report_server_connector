#########################################################
#
# Meta Data Ingestion From the Power BI Report Server
#
#########################################################
import logging
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

import datahub.emitter.mce_builder as builder
import requests
from datahub.configuration.common import AllowDenyPattern
from datahub.configuration.source_common import EnvBasedSourceConfigBase
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.api.common import PipelineContext
from datahub.ingestion.api.decorators import (
    SourceCapability,
    SupportStatus,
    capability,
    config_class,
    platform_name,
    support_status,
)
from datahub.ingestion.api.source import Source, SourceReport
from datahub.ingestion.api.workunit import MetadataWorkUnit
from datahub.metadata.com.linkedin.pegasus2avro.common import ChangeAuditStamps
from datahub.metadata.schema_classes import (
    BrowsePathsClass,
    ChangeTypeClass,
    CorpUserInfoClass,
    CorpUserKeyClass,
    DashboardInfoClass,
    DashboardKeyClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    StatusClass,
)
from orderedset import OrderedSet
from pydantic import BaseModel, validator
from pydantic.fields import Field
from requests_ntlm import HttpNtlmAuth

# Logger instance
LOGGER = logging.getLogger(__name__)


class CreatedFrom(Enum):
    REPORT = "Report"
    DATASET = "Dataset"
    VISUALIZATION = "Visualization"
    UNKNOWN = "UNKNOWN"


class CatalogItem(BaseModel):
    Id: str
    Name: str
    Description: Optional[str]
    Path: str
    Type: Any
    Hidden: bool
    Size: int
    ModifiedBy: Optional[str]
    ModifiedDate: Optional[datetime]
    CreatedBy: Optional[str]
    CreatedDate: Optional[datetime]
    ParentFolderId: Optional[str]
    ContentType: Optional[str]
    Content: str
    IsFavorite: bool

    def get_urn_part(self):
        return "reports.{}".format(self.Id)


class DataSet(CatalogItem):
    HasParameters: bool
    QueryExecutionTimeOut: int

    def get_urn_part(self):
        return "datasets.{}".format(self.Id)

    def __members(self):
        return (self.Id,)

    def __eq__(self, instance):
        return (
            isinstance(instance, DataSet) and self.__members() == instance.__members()
        )

    def __hash__(self):
        return hash(self.__members())


class DataModelDataSource(BaseModel):
    AuthType: Optional[str]
    SupportedAuthTypes: List[Optional[str]]
    Kind: Optional[Callable]
    ModelConnectionName: str
    Secret: str
    Type: Optional[str]
    Username: str


class CredentialsByUser(BaseModel):
    DisplayText: str
    UseAsWindowsCredentials: bool


class CredentialsInServer(BaseModel):
    UserName: str
    Password: str
    UseAsWindowsCredentials: bool
    ImpersonateAuthenticatedUser: bool


class ParameterValue(BaseModel):
    Name: str
    Value: str
    IsValueFieldReference: str


class ExtensionSettings(BaseModel):
    Extension: str
    ParameterValues: ParameterValue


class Subscription(BaseModel):
    Id: str
    Owner: str
    IsDataDriven: bool
    Description: str
    Report: str
    IsActive: bool
    EventType: str
    ScheduleDescription: str
    LastRunTime: datetime
    LastStatus: str
    ExtensionSettings: ExtensionSettings
    DeliveryExtension: str
    LocalizedDeliveryExtensionName: str
    ModifiedBy: str
    ModifiedDate: datetime
    ParameterValues: ParameterValue


class MetaData(BaseModel):
    is_relational: bool


class DataSource(CatalogItem):
    IsEnabled: bool
    DataModelDataSource: Optional[DataModelDataSource]
    DataSourceSubType: Optional[str]
    DataSourceType: Optional[str]
    IsOriginalConnectionStringExpressionBased: bool
    IsConnectionStringOverridden: bool
    CredentialsByUser: Optional[CredentialsByUser]
    CredentialsInServer: Optional[CredentialsInServer]
    IsReference: bool
    Subscriptions: Optional[Subscription]
    MetaData: Optional[MetaData]

    def __members(self):
        return (self.Id,)

    def __eq__(self, instance):
        return (
            isinstance(instance, DataSource)
            and self.__members() == instance.__members()
        )

    def __hash__(self):
        return hash(self.__members())


class Comment(BaseModel):
    Id: str
    ItemId: str
    UserName: str
    ThreadId: str
    AttachmentPath: str
    Text: str
    CreatedDate: datetime
    ModifiedDate: datetime


class ExcelWorkbook(CatalogItem):
    Comments: Comment


class Role(BaseModel):
    Name: str
    Description: str


class SystemPolicies(BaseModel):
    GroupUserName: str
    Roles: List[Role]
    DisplayName: Optional[str]

    @validator("DisplayName", always=True)
    def validate_diplay_name(cls, value, values):  # noqa: N805
        return values["GroupUserName"].split("\\")[-1]

    def get_urn_part(self):
        return "users.{}".format(self.GroupUserName)


class Report(CatalogItem):
    HasDataSources: bool
    HasSharedDataSets: bool
    HasParameters: bool
    UserInfo: Optional[SystemPolicies]


class PowerBiReport(CatalogItem):
    HasDataSources: bool


class Extension(BaseModel):
    ExtensionType: str
    Name: str
    LocalizedName: str
    Visible: bool


class Folder(CatalogItem):
    """Folder"""


class DrillThroughTarget(BaseModel):
    DrillThroughTargetType: str


class Value(BaseModel):
    Value: str
    Goal: int
    Status: int
    TrendSet: List[int]


class Kpi(CatalogItem):
    ValuerFormat: str
    Visualization: str
    DrillThroughTarget: DrillThroughTarget
    Currency: str
    Values: Value
    Data: Dict[str, str]


class LinkedReport(CatalogItem):
    HasParemeters: bool
    Link: str


class Manifest(BaseModel):
    Resorces: List[Dict[str, List]]


class MobileReport(CatalogItem):
    AllowCaching: bool
    Manifest: Manifest


class PowerBIReport(CatalogItem):
    HasDataSources: bool


class Resources(CatalogItem):
    """Resources"""


class System(BaseModel):
    ReportServerAbsoluteUrl: str
    ReportServerRelativeUrl: str
    WebPortalRelativeUrl: str
    ProductName: str
    ProductVersion: str
    ProductType: str
    TimeZone: str


class Constant:
    """
    keys used in powerbi plugin
    """

    DATASET = "DATASET"
    REPORTS = "REPORTS"
    REPORT = "REPORT"
    DATASOURCE = "DATASOURCE"
    DATASET_DATASOURCES = "DATASET_DATASOURCES"
    DatasetId = "DatasetId"
    ReportId = "ReportId"
    PowerBiReportId = "ReportId"
    Dataset_URN = "DatasetURN"
    DASHBOARD_ID = "powerbi.linkedin.com/dashboards/{}"
    DASHBOARD = "dashboard"
    DATASETS = "DATASETS"
    DATASET_ID = "powerbi.linkedin.com/datasets/{}"
    DATASET_PROPERTIES = "datasetProperties"
    SUBSCRIPTION = "SUBSCRIPTION"
    SYSTEM = "SYSTEM"
    CATALOG_ITEM = "CATALOG_ITEM"
    EXCEL_WORKBOOK = "EXCEL_WORKBOOK"
    EXTENSIONS = "EXTENSIONS"
    FAVORITE_ITEM = "FAVORITE_ITEM"
    FOLDERS = "FOLDERS"
    KPIS = "KPIS"
    LINKED_REPORTS = "LINKED_REPORTS"
    LINKED_REPORT = "LINKED_REPORT"
    ME = "ME"
    MOBILE_REPORTS = "MOBILE_REPORTS"
    MOBILE_REPORT = "MOBILE_REPORT"
    POWERBI_REPORTS = "POWERBI_REPORTS"
    POWERBI_REPORT = "POWERBI_REPORT"
    RESOURCE = "RESOURCE"
    SESSION = "SESSION"
    SYSTEM_POLICIES = "SYSTEM_POLICIES"
    DATASET_KEY = "datasetKey"
    BROWSERPATH = "browsePaths"
    DATAPLATFORM_INSTANCE = "dataPlatformInstance"
    STATUS = "status"
    VALUE = "value"
    ID = "ID"
    DASHBOARD_INFO = "dashboardInfo"
    DASHBOARD_KEY = "dashboardKey"
    CORP_USER = "corpuser"
    CORP_USER_INFO = "corpUserInfo"
    OWNERSHIP = "ownership"
    CORP_USER_KEY = "corpUs erKey"


class PowerBiReportServerAPIConfig(EnvBasedSourceConfigBase):
    username: str = Field(description="Windows account username")
    password: str = Field(description="Windows account password")
    workstation_name: str = Field(default="localhost", description="Workstation name")
    report_virtual_directory_name: str = Field(
        description="Report Virtual Directory URL name"
    )
    report_server_virtual_directory_name: str = Field(
        description="Report Server Virtual Directory URL name"
    )
    dataset_type_mapping: Dict[str, str] = Field(
        description="Mapping of Power BI datasource type to Datahub dataset."
    )
    scan_timeout: int = Field(
        default=60,
        description="time in seconds to wait for Power BI metadata scan result.",
    )

    @property
    def get_base_api_url(self):
        return "http://{}/{}/api/v2.0/".format(
            self.workstation_name, self.report_virtual_directory_name
        )


class PowerBiDashboardSourceConfig(PowerBiReportServerAPIConfig):
    platform_name: str = "powerbireportserver"
    platform_urn: str = builder.make_data_platform_urn(platform=platform_name)
    report_pattern: AllowDenyPattern = AllowDenyPattern.allow_all()
    chart_pattern: AllowDenyPattern = AllowDenyPattern.allow_all()


class PowerBiReportServerAPI:
    # API endpoints of PowerBi Report Server to fetch reports, datasets
    API_ENDPOINTS = {
        Constant.CATALOG_ITEM: "{PBIRS_BASE_URL}/CatalogItems({CATALOG_ID})",
        Constant.DATASETS: "{PBIRS_BASE_URL}/Datasets",
        Constant.DATASET: "{PBIRS_BASE_URL}/Datasets({DATASET_ID})",
        Constant.DATASET_DATASOURCES: "{PBIRS_BASE_URL}/Datasets({DATASET_ID})/DataSources",
        Constant.DATASOURCE: "{PBIRS_BASE_URL}/DataSources({DATASOURCE_ID})",
        Constant.EXCEL_WORKBOOK: "{PBIRS_BASE_URL}/ExcelWorkbooks({EXCEL_WORKBOOK_ID})",
        Constant.EXTENSIONS: "{PBIRS_BASE_URL}/Extensions",
        Constant.FAVORITE_ITEM: "{PBIRS_BASE_URL}/FavoriteItems({FAVORITE_ITEM_ID})",
        Constant.FOLDERS: "{PBIRS_BASE_URL}/Folders({FOLDER_ID})",
        Constant.KPIS: "{PBIRS_BASE_URL}/Kpis({KPI_ID})",
        Constant.LINKED_REPORTS: "{PBIRS_BASE_URL}/LinkedReports",
        Constant.LINKED_REPORT: "{PBIRS_BASE_URL}/LinkedReports({LINKED_REPORT_ID})",
        Constant.ME: "{PBIRS_BASE_URLL}/Me",
        Constant.MOBILE_REPORTS: "{PBIRS_BASE_URL}/MobileReports",
        Constant.MOBILE_REPORT: "{PBIRS_BASE_URL}/MobileReports({MOBILE_REPORT_ID})",
        Constant.POWERBI_REPORTS: "{PBIRS_BASE_URL}/PowerBiReports",
        Constant.POWERBI_REPORT: "{PBIRS_BASE_URL}/PowerBiReports({POWERBI_REPORT_ID})",
        Constant.REPORTS: "{PBIRS_BASE_URL}/Reports",
        Constant.REPORT: "{PBIRS_BASE_URL}/Reports({REPORT_ID})",
        Constant.RESOURCE: "{PBIRS_BASE_URL}/Resources({RESOURCE_GET})",
        Constant.SESSION: "{PBIRS_BASE_URL}/Session",
        Constant.SUBSCRIPTION: "{PBIRS_BASE_URL}/Subscriptions({SUBSCRIPTION_ID})",
        Constant.SYSTEM: "{PBIRS_BASE_URL}/System",
        Constant.SYSTEM_POLICIES: "{PBIRS_BASE_URL}/System/Policies",
    }

    def __init__(self, config: PowerBiReportServerAPIConfig) -> None:
        self.__config: PowerBiReportServerAPIConfig = config
        self.__auth: HttpNtlmAuth = HttpNtlmAuth(
            "{}\\{}".format(self.__config.workstation_name, self.__config.username),
            self.__config.password,
        )

    def get_auth_credentials(self):
        return self.__auth

    def get_users_policies(self) -> List[SystemPolicies]:
        """
        Get user policy by Power Bi Report Server System
        """
        user_list_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.SYSTEM_POLICIES
        ]
        # Replace place holders
        user_list_endpoint = user_list_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url
        )
        # Hit PowerBi
        LOGGER.info("Request to URL={}".format(user_list_endpoint))
        response = requests.get(
            url=user_list_endpoint, auth=self.get_auth_credentials()
        )

        # Check if we got response from PowerBi
        if response.status_code != 200:
            LOGGER.warning(
                "Failed to fetch user list from power-bi for, http_status={}, message={}".format(
                    response.status_code, response.text
                )
            )
            raise ConnectionError("Failed to fetch the user list from the power-bi")

        users_dict: List[Any] = response.json()[Constant.VALUE]
        # Iterate through response and create a list of PowerBiAPI.Dashboard
        users: List[SystemPolicies] = [
            SystemPolicies.parse_obj(instance) for instance in users_dict
        ]
        return users

    def get_user_policies(self, user_name: str) -> Optional[SystemPolicies]:
        users_policies = self.get_users_policies()
        for user_policy in users_policies:
            if user_policy.GroupUserName == user_name:
                return user_policy
        return None

    def get_report(self, report_id: str) -> Optional[Report]:
        """
        Fetch the .rdl report from PowerBiReportServer for the given report id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        report_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[Constant.REPORT]
        # Replace place holders
        report_get_endpoint = report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to report URL={}".format(report_get_endpoint))
        response = requests.get(
            url=report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return Report.parse_obj(response_dict)

    def get_powerbi_report(self, report_id: str) -> Optional[PowerBiReport]:
        """
        Fetch the .pbix report from PowerBiReportServer for the given report id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        powerbi_report_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.POWERBI_REPORT
        ]
        # Replace place holders
        powerbi_report_get_endpoint = powerbi_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            POWERBI_REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to report URL={}".format(powerbi_report_get_endpoint))
        response = requests.get(
            url=powerbi_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()
        return PowerBiReport.parse_obj(response_dict)

    def get_linked_report(self, report_id: str) -> Optional[LinkedReport]:
        """
        Fetch the mobile report from PowerBiReportServer for the given report id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        linked_report_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.LINKED_REPORT
        ]
        # Replace place holders
        linked_report_get_endpoint = linked_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            LINKED_REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to report URL={}".format(linked_report_get_endpoint))
        response = requests.get(
            url=linked_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return LinkedReport.parse_obj(response_dict)

    def get_mobile_report(self, report_id: str) -> Optional[MobileReport]:
        """
        Fetch the mobile report from PowerBiReportServer for the given report id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        mobile_report_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.MOBILE_REPORT
        ]
        # Replace place holders
        mobile_report_get_endpoint = mobile_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            MOBILE_REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to report URL={}".format(mobile_report_get_endpoint))
        response = requests.get(
            url=mobile_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return MobileReport.parse_obj(response_dict)

    def get_all_reports(self) -> List[Any]:
        """
        Fetch all reports from PowerBiReportServer
        """
        report_types_mapping: Dict[str, Any] = {
            Constant.REPORTS: Report,
            Constant.MOBILE_REPORTS: MobileReport,
            Constant.LINKED_REPORTS: LinkedReport,
            Constant.POWERBI_REPORTS: PowerBiReport,
        }

        reports: List[Any] = []
        for report_type in report_types_mapping.keys():

            report_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[report_type]
            # Replace place holders
            report_get_endpoint = report_get_endpoint.format(
                PBIRS_BASE_URL=self.__config.get_base_api_url,
            )
            # Hit PowerBiReportServer
            LOGGER.info("Request to report URL={}".format(report_get_endpoint))
            response = requests.get(
                url=report_get_endpoint,
                auth=self.get_auth_credentials(),
            )

            # Check if we got response from PowerBi
            if response.status_code != 200:
                message: str = "Failed to fetch report from power-bi-report-server for"
                LOGGER.warning(message)
                LOGGER.warning("{}={}".format(Constant.ReportId, report_type))

            response_dict = response.json()["value"]
            if response_dict:
                reports.extend(
                    report_types_mapping[report_type].parse_obj(report)
                    for report in response_dict
                )

        return reports

    def get_dataset(self, dataset_id: str) -> Any:
        """
        Fetch the dataset from PowerBi for the given dataset identifier
        """
        if dataset_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.DatasetId, dataset_id))
            return None

        dataset_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.DATASET
        ]
        # Replace place holders
        dataset_get_endpoint = dataset_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            DATASET_ID=dataset_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to dataset URL={}".format(dataset_get_endpoint))
        response = requests.get(
            url=dataset_get_endpoint, auth=self.get_auth_credentials()
        )
        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch dataset from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.DatasetId, dataset_id))
            raise ConnectionError(message)

        response_dict = response.json()

        # PowerBi Always return the webURL,
        # in-case if it is None then setting complete webURL to None instead of None/details
        return DataSet.parse_obj(response_dict)

    def get_data_source(self, dataset: DataSet) -> Any:
        """
        Fetch the data source from PowerBi for the given dataset
        """

        datasource_get_endpoint: str = PowerBiReportServerAPI.API_ENDPOINTS[
            Constant.DATASET_DATASOURCES
        ]
        # Replace place holders
        datasource_get_endpoint = datasource_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            DATASET_ID=dataset.Id,
        )
        # Hit PowerBi
        LOGGER.info("Request to datasource URL={}".format(datasource_get_endpoint))
        response = requests.get(
            url=datasource_get_endpoint, auth=self.get_auth_credentials()
        )
        # Check if we got response from PowerBi
        if response.status_code != 200:
            message: str = "Failed to fetch datasource from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.DatasetId, dataset.Id))
            raise ConnectionError(message)

        res = response.json()
        value = res["value"]
        if len(value) == 0:
            LOGGER.info(
                "datasource is not found for dataset {}({})".format(
                    dataset.Name, dataset.Id
                )
            )
            return None
        # Consider only zero index datasource
        datasource_dict = value[0]
        # Create datasource instance with basic detail available
        datasource = DataSource.parse_obj(datasource_dict)

        # Check if datasource is relational as per our relation mapping
        if self.__config.dataset_type_mapping.get(datasource.Type) is not None:
            # Now set the database detail as it is relational data source
            datasource.MetaData = MetaData(is_relational=True)
            datasource.database = datasource_dict["connectionDetails"]["database"]
            datasource.server = datasource_dict["connectionDetails"]["server"]
        else:
            datasource.MetaData = MetaData(is_relational=False)

        return datasource


class Mapper:
    """
    Transfrom PowerBi Report Server concept Report to DataHub concept Dashboard
    """

    class EquableMetadataWorkUnit(MetadataWorkUnit):
        """
        We can add EquableMetadataWorkUnit to set.
        This will avoid passing same MetadataWorkUnit to DataHub Ingestion framework.
        """

        def __eq__(self, instance):
            return self.id == self.id

        def __hash__(self):
            return id(self.id)

    def __init__(self, config: PowerBiDashboardSourceConfig):
        self.__config = config

    @staticmethod
    def new_mcp(
        entity_type,
        entity_urn,
        aspect_name,
        aspect,
        change_type=ChangeTypeClass.UPSERT,
    ):
        """
        Create MCP
        """
        return MetadataChangeProposalWrapper(
            entityType=entity_type,
            changeType=change_type,
            entityUrn=entity_urn,
            aspectName=aspect_name,
            aspect=aspect,
        )

    def __to_work_unit(
        self, mcp: MetadataChangeProposalWrapper
    ) -> EquableMetadataWorkUnit:
        return Mapper.EquableMetadataWorkUnit(
            id="{PLATFORM}-{ENTITY_URN}-{ASPECT_NAME}".format(
                PLATFORM=self.__config.platform_name,
                ENTITY_URN=mcp.entityUrn,
                ASPECT_NAME=mcp.aspectName,
            ),
            mcp=mcp,
        )

    @staticmethod
    def to_urn_set(mcps: List[MetadataChangeProposalWrapper]) -> List[str]:
        return list(
            OrderedSet(
                [
                    mcp.entityUrn
                    for mcp in mcps
                    if mcp is not None and mcp.entityUrn is not None
                ]
            )
        )

    def __to_datahub_dashboard(
        self,
        report: Report,
        chart_mcps: List[MetadataChangeProposalWrapper],
        user_mcps: List[MetadataChangeProposalWrapper],
    ) -> List[MetadataChangeProposalWrapper]:
        """
        Map PowerBi dashboard to Datahub dashboard
        """

        dashboard_urn = builder.make_dashboard_urn(
            self.__config.platform_name, report.get_urn_part()
        )

        chart_urn_list: List[str] = self.to_urn_set(chart_mcps)
        user_urn_list: List[str] = self.to_urn_set(user_mcps)

        def chart_custom_properties(
            _report: Report,
        ) -> dict:
            return {
                "chartCount": 0,  # str(len(dashboard.tiles)), couldn't count charts
                "workspaceName": "",
                "workspaceId": _report.Id,
            }

        # DashboardInfo mcp
        dashboard_info_cls = DashboardInfoClass(
            description=report.Name or "",
            title=report.Name or "",
            charts=chart_urn_list,
            lastModified=ChangeAuditStamps(),
            dashboardUrl=report.Path,  # should be werbUrl
            customProperties={**chart_custom_properties(report)},
        )

        info_mcp = self.new_mcp(
            entity_type=Constant.DASHBOARD,
            entity_urn=dashboard_urn,
            aspect_name=Constant.DASHBOARD_INFO,
            aspect=dashboard_info_cls,
        )

        # removed status mcp
        removed_status_mcp = self.new_mcp(
            entity_type=Constant.DASHBOARD,
            entity_urn=dashboard_urn,
            aspect_name=Constant.STATUS,
            aspect=StatusClass(removed=False),
        )

        # dashboardKey mcp
        dashboard_key_cls = DashboardKeyClass(
            dashboardTool=self.__config.platform_name,
            dashboardId=Constant.DASHBOARD_ID.format(report.Id),
        )

        # Dashboard key
        dashboard_key_mcp = self.new_mcp(
            entity_type=Constant.DASHBOARD,
            entity_urn=dashboard_urn,
            aspect_name=Constant.DASHBOARD_KEY,
            aspect=dashboard_key_cls,
        )

        # Dashboard Ownership
        owners = [
            OwnerClass(owner=user_urn, type=OwnershipTypeClass.CONSUMER)
            for user_urn in user_urn_list
            if user_urn is not None
        ]
        ownership = OwnershipClass(owners=owners)
        # Dashboard owner MCP
        owner_mcp = self.new_mcp(
            entity_type=Constant.DASHBOARD,
            entity_urn=dashboard_urn,
            aspect_name=Constant.OWNERSHIP,
            aspect=ownership,
        )

        # Dashboard browsePaths
        browse_path = BrowsePathsClass(
            paths=["/powerbi/{}".format(self.__config.platform_name)]
        )
        browse_path_mcp = self.new_mcp(
            entity_type=Constant.DASHBOARD,
            entity_urn=dashboard_urn,
            aspect_name=Constant.BROWSERPATH,
            aspect=browse_path,
        )

        return [
            browse_path_mcp,
            info_mcp,
            removed_status_mcp,
            dashboard_key_mcp,
            owner_mcp,
        ]

    def to_datahub_user(
        self, user: SystemPolicies
    ) -> List[MetadataChangeProposalWrapper]:
        """
        Map PowerBiReportServer user to datahub user
        """

        LOGGER.info("Converting user {} to datahub's user".format(user.GroupUserName))

        # Create an URN for user
        user_urn = builder.make_user_urn(user.get_urn_part())

        user_info_instance = CorpUserInfoClass(
            displayName=user.DisplayName,
            email=None,  # user.emailAddress
            title=user.DisplayName,
            active=True,
        )

        info_mcp = self.new_mcp(
            entity_type=Constant.CORP_USER,
            entity_urn=user_urn,
            aspect_name=Constant.CORP_USER_INFO,
            aspect=user_info_instance,
        )

        # removed status mcp
        status_mcp = self.new_mcp(
            entity_type=Constant.CORP_USER,
            entity_urn=user_urn,
            aspect_name=Constant.STATUS,
            aspect=StatusClass(removed=False),
        )

        user_key = CorpUserKeyClass(
            username=user.GroupUserName
        )  # should be user id here

        user_key_mcp = self.new_mcp(
            entity_type=Constant.CORP_USER,
            entity_urn=user_urn,
            aspect_name=Constant.CORP_USER_KEY,
            aspect=user_key,
        )

        return [info_mcp, status_mcp, user_key_mcp]

    def to_datahub_work_units(self, report: Report) -> Set[EquableMetadataWorkUnit]:
        mcps = []

        LOGGER.info("Converting dashboard={} to datahub dashboard".format(report.Name))

        # Convert user to CorpUser
        user_mcps = self.to_datahub_user(report.UserInfo)
        # Convert tiles to charts
        ds_mcps: List[Any]
        chart_mcps: List[Any]
        ds_mcps, chart_mcps = [], []  # self.to_datahub_chart(dashboard.tiles)
        # Lets convert dashboard to datahub dashboard
        dashboard_mcps = self.__to_datahub_dashboard(report, chart_mcps, user_mcps)

        # Now add MCPs in sequence
        mcps.extend(ds_mcps)
        mcps.extend(user_mcps)
        mcps.extend(chart_mcps)
        mcps.extend(dashboard_mcps)

        # Convert MCP to work_units
        work_units = map(self.__to_work_unit, mcps)
        # Return set of work_unit
        return OrderedSet([wu for wu in work_units if wu is not None])


@dataclass
class PowerBiReportServerDashboardSourceReport(SourceReport):
    scanned_report: int = 0
    filtered_reports: List[str] = dataclass_field(default_factory=list)

    def report_scanned(self, count: int = 1) -> None:
        self.scanned_report += count

    def report_dropped(self, view: str) -> None:
        self.filtered_reports.append(view)


@platform_name("PowerBIReportServer")
@config_class(PowerBiDashboardSourceConfig)
@support_status(SupportStatus.UNKNOWN)
@capability(SourceCapability.OWNERSHIP, "Enabled by default")
class PowerBiReportServerDashboardSource(Source):
    """
        This plugin extracts the following:

    - Power BI dashboards, tiles, datasets
    - Names, descriptions and URLs of dashboard and tile
    - Owners of dashboards

    ## Configuration Notes

    See the
    1.  [Microsoft AD App Creation doc]
        (https://docs.microsoft.com/en-us/power-bi/developer/embedded/embed-service-principal)
        for the steps to create a app client ID and secret.
    2.  Login to Power BI as Admin and from `Tenant settings` allow below permissions.
        - Allow service principles to use Power BI APIs
        - Allow service principals to use read-only Power BI admin APIs
        - Enhance admin APIs responses with detailed metadata
    """

    source_config: PowerBiDashboardSourceConfig
    report: PowerBiReportServerDashboardSourceReport
    accessed_dashboards: int = 0

    def __init__(self, config: PowerBiDashboardSourceConfig, ctx: PipelineContext):
        super().__init__(ctx)
        self.source_config = config
        self.report = PowerBiReportServerDashboardSourceReport()
        self.auth = PowerBiReportServerAPI(self.source_config).get_auth_credentials()
        self.powerbi_client = PowerBiReportServerAPI(self.source_config)
        self.mapper = Mapper(config)

    @classmethod
    def create(cls, config_dict, ctx):
        config = PowerBiDashboardSourceConfig.parse_obj(config_dict)
        return cls(config, ctx)

    def get_workunits(self) -> Iterable[MetadataWorkUnit]:
        """
        Datahub Ingestion framework invoke this method
        """
        LOGGER.info("PowerBiReportServer plugin execution is started")

        # Fetch PowerBiReportServer reports for given url
        # workspace = self.powerbi_client.get_workspace(self.source_config.workspace_id)
        reports = self.powerbi_client.get_all_reports()

        for report in reports:
            try:
                # Fetch PowerBi users for dashboards
                report.UserInfo = self.powerbi_client.get_user_policies(
                    report.CreatedBy
                )
                # Increase dashboard and tiles count in report
                self.report.report_scanned(count=len(report))
            except Exception as e:
                message = "Error ({}) occurred while loading dashboard {}(id={}) tiles.".format(
                    e, report.Name, report.Id
                )
                LOGGER.exception(message, e)
                self.report.report_warning(report.Id, message)

            # Convert PowerBi Dashboard and child entities
            # to Datahub work unit to ingest into Datahub
            workunits = self.mapper.to_datahub_work_units(report)
            for workunit in workunits:
                # Add workunit to report
                self.report.report_workunit(workunit)
                # Return workunit to Datahub Ingestion framework
                yield workunit

    def get_report(self) -> SourceReport:
        return self.report

    def close(self):
        pass
