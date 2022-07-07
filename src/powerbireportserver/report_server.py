#########################################################
#
# Meta Data Ingestion From the Power BI Report Server
#
#########################################################
import logging
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, Iterable, List, Optional, Set, Union

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
    DatasetPropertiesClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    StatusClass,
)
from orderedset import OrderedSet
from pydantic import ValidationError
from pydantic.fields import Field
from requests_ntlm import HttpNtlmAuth

# Logger instance
from .constants import API_ENDPOINTS, Constant
from .graphql_domain import CorpUser
from .report_server_domain import (
    DataSet,
    DataSource,
    LinkedReport,
    MetaData,
    MobileReport,
    PowerBiReport,
    Report,
    SystemPolicies,
)

LOGGER = logging.getLogger(__name__)


class PowerBiReportServerAPIConfig(EnvBasedSourceConfigBase):
    username: str = Field(description="Windows account username")
    password: str = Field(description="Windows account password")
    workstation_name: str = Field(default="localhost", description="Workstation name")
    host_port: str = Field(description="Power BI Report Server host URL")
    server_alias: str = Field(
        default="", description="Alias for Power BI Report Server host URL"
    )
    graphql_url: str = Field(description="GraphQL API URL")
    report_virtual_directory_name: str = Field(
        description="Report Virtual Directory URL name"
    )
    report_server_virtual_directory_name: str = Field(
        description="Report Server Virtual Directory URL name"
    )
    dataset_type_mapping: Dict[str, str] = Field(
        default={},
        description="Mapping of Power BI DataSource type to Datahub DataSet.",
    )
    scan_timeout: int = Field(
        default=60,
        description="time in seconds to wait for Power BI metadata scan result.",
    )

    @property
    def get_base_api_url(self):
        return "http://{}/{}/api/v2.0/".format(
            self.host_port, self.report_virtual_directory_name
        )

    @property
    def get_base_url(self):
        return "http://{}/{}/".format(
            self.host_port, self.report_virtual_directory_name
        )

    @property
    def host(self):
        return self.server_alias or self.host_port.split(":")[0]


class PowerBiReportServerDashboardSourceConfig(PowerBiReportServerAPIConfig):
    platform_name: str = "powerbi"
    platform_urn: str = builder.make_data_platform_urn(platform=platform_name)
    report_pattern: AllowDenyPattern = AllowDenyPattern.allow_all()
    chart_pattern: AllowDenyPattern = AllowDenyPattern.allow_all()


class PowerBiReportServerAPI:
    # API endpoints of PowerBi Report Server to fetch reports, datasets

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
        Get User policy by Power Bi Report Server System
        """
        user_list_endpoint: str = API_ENDPOINTS[Constant.SYSTEM_POLICIES]
        # Replace place holders
        user_list_endpoint = user_list_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url
        )
        # Hit PowerBi Report Server
        LOGGER.info("Request to URL={}".format(user_list_endpoint))
        response = requests.get(
            url=user_list_endpoint, auth=self.get_auth_credentials()
        )

        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            LOGGER.warning(
                "Failed to fetch User list from power-bi for, http_status={}, message={}".format(
                    response.status_code, response.text
                )
            )
            raise ConnectionError("Failed to fetch the User list from the power-bi")

        users_dict: List[Any] = response.json()[Constant.VALUE]
        # Iterate through response and create a list of PowerBiReportServerAPI.Dashboard
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
        Fetch the .rdl Report from PowerBiReportServer for the given Report Id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        report_get_endpoint: str = API_ENDPOINTS[Constant.REPORT]
        # Replace place holders
        report_get_endpoint = report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to Report URL={}".format(report_get_endpoint))
        response = requests.get(
            url=report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch Report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return Report.parse_obj(response_dict)

    def get_powerbi_report(self, report_id: str) -> Optional[PowerBiReport]:
        """
        Fetch the .pbix Report from PowerBiReportServer for the given Report Id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        powerbi_report_get_endpoint: str = API_ENDPOINTS[Constant.POWERBI_REPORT]
        # Replace place holders
        powerbi_report_get_endpoint = powerbi_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            POWERBI_REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to Report URL={}".format(powerbi_report_get_endpoint))
        response = requests.get(
            url=powerbi_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch Report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()
        return PowerBiReport.parse_obj(response_dict)

    def get_linked_report(self, report_id: str) -> Optional[LinkedReport]:
        """
        Fetch the Mobile Report from PowerBiReportServer for the given Report Id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        linked_report_get_endpoint: str = API_ENDPOINTS[Constant.LINKED_REPORT]
        # Replace place holders
        linked_report_get_endpoint = linked_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            LINKED_REPORT_ID=report_id,
        )
        # Hit PowerBiReportServer
        LOGGER.info("Request to Report URL={}".format(linked_report_get_endpoint))
        response = requests.get(
            url=linked_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch Report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return LinkedReport.parse_obj(response_dict)

    def get_mobile_report(self, report_id: str) -> Optional[MobileReport]:
        """
        Fetch the Mobile Report from PowerBiReportServer for the given Report Id
        """
        if report_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.ReportId, report_id))
            return None

        mobile_report_get_endpoint: str = API_ENDPOINTS[Constant.MOBILE_REPORT]
        # Replace place holders
        mobile_report_get_endpoint = mobile_report_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            MOBILE_REPORT_ID=report_id,
        )
        # Hit PowerBi ReportServer
        LOGGER.info("Request to Report URL={}".format(mobile_report_get_endpoint))
        response = requests.get(
            url=mobile_report_get_endpoint,
            auth=self.get_auth_credentials(),
        )

        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch Report from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.ReportId, report_id))
            raise ConnectionError(message)

        response_dict = response.json()

        return MobileReport.parse_obj(response_dict)

    def get_all_reports(self) -> List[Any]:
        """
        Fetch all Reports from PowerBiReportServer
        """
        report_types_mapping: Dict[str, Any] = {
            Constant.REPORTS: Report,
            Constant.MOBILE_REPORTS: MobileReport,
            Constant.LINKED_REPORTS: LinkedReport,
            Constant.POWERBI_REPORTS: PowerBiReport,
        }

        reports: List[Any] = []
        for report_type in report_types_mapping.keys():

            report_get_endpoint: str = API_ENDPOINTS[report_type]
            # Replace place holders
            report_get_endpoint = report_get_endpoint.format(
                PBIRS_BASE_URL=self.__config.get_base_api_url,
            )
            # Hit PowerBi ReportServer
            LOGGER.info("Request to Report URL={}".format(report_get_endpoint))
            response = requests.get(
                url=report_get_endpoint,
                auth=self.get_auth_credentials(),
            )

            # Check if we got response from PowerBi Report Server
            if response.status_code != 200:
                message: str = "Failed to fetch Report from power-bi-report-server for"
                LOGGER.warning(message)
                LOGGER.warning("{}={}".format(Constant.ReportId, report_type))
                raise ValueError(message)

            response_dict = response.json()["value"]
            if response_dict:
                reports.extend(
                    report_types_mapping[report_type].parse_obj(report)
                    for report in response_dict
                )

        return reports

    def get_dataset(self, dataset_id: str) -> Any:
        """
        Fetch the DataSet from PowerBi Report Server for the given DataSet identifier
        """
        if dataset_id is None:
            LOGGER.info("Input value is None")
            LOGGER.info("{}={}".format(Constant.DatasetId, dataset_id))
            return None

        dataset_get_endpoint: str = API_ENDPOINTS[Constant.DATASET]
        # Replace place holders
        dataset_get_endpoint = dataset_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            DATASET_ID=dataset_id,
        )
        # Hit PowerBi ReportServer
        LOGGER.info("Request to DataSet URL={}".format(dataset_get_endpoint))
        response = requests.get(
            url=dataset_get_endpoint, auth=self.get_auth_credentials()
        )
        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch DataSet from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.DatasetId, dataset_id))
            raise ConnectionError(message)

        response_dict = response.json()

        # PowerBi Always return the webURL,
        # in-case if it is None then setting complete webURL to None instead of None/details
        return DataSet.parse_obj(response_dict)

    def get_data_source(self, datasource_id: str) -> Any:
        """
        Fetch the data source from PowerBi for the given dataset
        """

        datasource_get_endpoint: str = API_ENDPOINTS[Constant.DATASET_DATASOURCES]
        # Replace place holders
        datasource_get_endpoint = datasource_get_endpoint.format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            DATASET_ID=datasource_id,
        )
        # Hit PowerBi Report Server
        LOGGER.info("Request to DataSource URL={}".format(datasource_get_endpoint))
        response = requests.get(
            url=datasource_get_endpoint, auth=self.get_auth_credentials()
        )
        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch DataSource from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.DatasetId, datasource_id))
            raise ConnectionError(message)

        res = response.json()
        value = res["value"]
        if len(value) == 0:
            LOGGER.info(
                "DataSource is not found for DataSource id {}".format(datasource_id)
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
        else:
            datasource.MetaData = MetaData(is_relational=False)

        return datasource

    def get_report_data_sources(
        self, report: Union[Report, PowerBiReport]
    ) -> List[DataSource]:
        """
        Fetch the data source from PowerBi for the given dataset
        """
        report_types_mapping: Dict[str, Any] = {
            Constant.TYPE_REPORT: API_ENDPOINTS[Constant.REPORT_DATASOURCES],
            Constant.TYPE_POWERBI_REPORT: API_ENDPOINTS[
                Constant.POWERBI_REPORT_DATASOURCES
            ],
        }
        data_sources: List[DataSource] = []
        # datasource_get_endpoint: str = API_ENDPOINTS[Constant.DATASET_DATASOURCES]
        # Replace place holders
        datasource_get_endpoint = report_types_mapping[report.Type].format(
            PBIRS_BASE_URL=self.__config.get_base_api_url,
            ID=report.Id,
        )
        # Hit PowerBi Report Server
        LOGGER.info("Request to DataSources URL={}".format(datasource_get_endpoint))
        response = requests.get(
            url=datasource_get_endpoint, auth=self.get_auth_credentials()
        )
        # Check if we got response from PowerBi Report Server
        if response.status_code != 200:
            message: str = "Failed to fetch DataSource from power-bi-report-server for"
            LOGGER.warning(message)
            LOGGER.warning("{}={}".format(Constant.DatasetId, report.Id))
            raise ConnectionError(message)

        res = response.json()
        values = res["value"]
        if len(values) == 0:
            LOGGER.info(
                "No DataSources found for Report {}({})".format(report.Name, report.Id)
            )
            return data_sources
        # Consider only zero index datasource
        if values:
            for value in values:
                data_source = DataSource.parse_obj(value)
                if (
                    data_source.DataSourceType
                    and self.__config.dataset_type_mapping.get(
                        data_source.DataSourceType
                    )
                    is not None
                ):
                    # Now set the database detail as it is relational data source
                    data_source.MetaData = MetaData(is_relational=True)
                else:
                    data_source.MetaData = MetaData(is_relational=False)
                data_sources.append(data_source)
            # datasource_dict = value[0]
            # Create datasource instance with basic detail available
            # datasource = DataSource.parse_obj(datasource_dict)

            # Check if datasource is relational as per our relation mapping

            return data_sources
        return data_sources


class UserDao:
    def __init__(self, config: PowerBiReportServerDashboardSourceConfig):
        self.__config = config

    def _run_query(self, query) -> Dict[str, Any]:
        request = requests.post(url=self.__config.graphql_url, json={"query": query})
        if request.status_code == 200:
            return request.json()
        else:
            raise Exception(
                "Query failed to run by returning code of {}. {}".format(
                    request.status_code, query
                )
            )

    def _get_owner_by_name(self, user_name: str) -> Dict[str, Any]:
        get_owner = f"""{{
        search(input: {{ type: CORP_USER, query: "{user_name}"}}){{
            searchResults{{
                entity{{
                     urn
                     type
                     ...on CorpUser {{
                        username
                        urn
                        type
                        properties {{
                            active
                            displayName
                            email
                            title
                        }}
                    }}
                }}
              matchedFields {{
                name
                value
                }}
            }}
        }}
    }}
    """

        result = self._run_query(get_owner)
        return result

    @staticmethod
    def _filter_values(response: Dict[str, Any], mask: str) -> Optional[Dict[str, Any]]:
        users = response["data"]["search"]["searchResults"]
        for user in users:
            if user["matchedFields"][0]["value"] == mask:
                return user["entity"]
        return None

    def get_owner_by_name(self, user_name: str) -> Optional[CorpUser]:
        response = self._get_owner_by_name(user_name=user_name)
        filtered_response = self._filter_values(response=response, mask=user_name)
        if filtered_response:
            return CorpUser.parse_obj(filtered_response)

        user_data = dict(
            urn=f"urn:li:corpuser:{user_name}",
            type=Constant.CORP_USER,
            username=user_name,
            properties=dict(active=True, displayName=user_name, email=""),
        )
        return CorpUser.parse_obj(user_data)


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

    def __init__(self, config: PowerBiReportServerDashboardSourceConfig):
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

    # def __to_datahub_dataset(
    #     self, report: Report
    # ) -> List[MetadataChangeProposalWrapper]:
    #     """
    #     Map PowerBi Report Server report DataSources to datahub dataset.
    #     Here we are mapping each table of PowerBi Report Server DataSource to Datahub dataset.
    #     """
    #
    #     dataset_mcps: List[MetadataChangeProposalWrapper] = []
    #     if not report.HasDataSources:
    #         return dataset_mcps
    #
    #     # We are only suporting relation PowerBi Report Server DataSources
    #     if not report.DataSources or report.DataSources.MetaData.is_relational is False:
    #         LOGGER.warning(
    #             "Report {}({}) has not relational DataSource".format(
    #                 report.Name, report.Id
    #             )
    #         )
    #         return dataset_mcps
    #
    #     LOGGER.info(
    #         "Converting Report DataSource, Report={}(id={}) to DataHub DataSet".format(
    #             report.Name, report.Id
    #         )
    #     )
    #
    #     for datasource in report.DataSources:
    #         # Create an URN for dataset
    #
    #         ds_urn = builder.make_dataset_urn(
    #             platform=self.__config.dataset_type_mapping[datasource.type],
    #             name="{}.{}.{}".format(
    #                 datasource.datasource.database,
    #                 datasource.schema_name,
    #                 datasource.name,
    #             ),
    #             env=self.__config.env,
    #         )
    #         LOGGER.info("{}={}".format(Constant.Dataset_URN, ds_urn))
    #         # Create datasetProperties mcp
    #         ds_properties = DatasetPropertiesClass(description=datasource.name)
    #
    #         info_mcp = self.new_mcp(
    #             entity_type=Constant.DATASET,
    #             entity_urn=ds_urn,
    #             aspect_name=Constant.DATASET_PROPERTIES,
    #             aspect=ds_properties,
    #         )
    #
    #         # Remove status mcp
    #         status_mcp = self.new_mcp(
    #             entity_type=Constant.DATASET,
    #             entity_urn=ds_urn,
    #             aspect_name=Constant.STATUS,
    #             aspect=StatusClass(removed=False),
    #         )
    #
    #         dataset_mcps.extend([info_mcp, status_mcp])
    #
    #     return dataset_mcps

    def __to_datahub_dashboard(
        self,
        report: Report,
        chart_mcps: List[MetadataChangeProposalWrapper],
        user_mcps: List[MetadataChangeProposalWrapper],
    ) -> List[MetadataChangeProposalWrapper]:
        """
        Map PowerBi Report Server report to Datahub Dashboard
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
                "chartCount": str(0),
                "workspaceName": "PowerBI Report Server",
                "workspaceId": self.__config.host_port,
                "dataSource": str(
                    [report.ConnectionString for report in _report.DataSources]
                )
                if _report.DataSources
                else "",
            }

        # DashboardInfo mcp
        dashboard_info_cls = DashboardInfoClass(
            description=report.Description or "",
            title=report.Name or "",
            charts=chart_urn_list,
            lastModified=ChangeAuditStamps(),
            dashboardUrl=report.get_web_url(self.__config.get_base_url),
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
            OwnerClass(owner=user_urn, type=OwnershipTypeClass.BUSINESS_OWNER)
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
            paths=[report.get_browse_path("powerbi_report_server", self.__config.host)]
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

    def to_datahub_user(self, user: CorpUser) -> List[MetadataChangeProposalWrapper]:
        """
        Map PowerBi ReportServer user to datahub user
        """
        LOGGER.info("Converting user {} to datahub's user".format(user.username))

        # Create an URN for User
        user_urn = builder.make_user_urn(user.get_urn_part())

        user_info_instance = CorpUserInfoClass(
            displayName=user.properties.displayName,
            email=user.properties.email,
            title=user.properties.title,
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

        user_key = CorpUserKeyClass(username=user.username)

        user_key_mcp = self.new_mcp(
            entity_type=Constant.CORP_USER,
            entity_urn=user_urn,
            aspect_name=Constant.CORP_USER_KEY,
            aspect=user_key,
        )

        return [info_mcp, status_mcp, user_key_mcp]

    def to_datahub_work_units(self, report: Report) -> Set[EquableMetadataWorkUnit]:
        mcps = []

        LOGGER.info("Converting Dashboard={} to DataHub Dashboard".format(report.Name))
        # Convert user to CorpUser
        user_mcps = self.to_datahub_user(report.UserInfo)
        # Convert tiles to charts
        ds_mcps: List[Any]
        chart_mcps: List[Any]
        # ds_mcps = self.__to_datahub_dataset(report)
        chart_mcps, ds_mcps = [], []  # self.to_datahub_chart(dashboard.tiles)
        # Lets convert Dashboard to DataHub Dashboard
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
@config_class(PowerBiReportServerDashboardSourceConfig)
@support_status(SupportStatus.UNKNOWN)
@capability(SourceCapability.OWNERSHIP, "Enabled by default")
class PowerBiReportServerDashboardSource(Source):
    """
        This plugin extracts the following:

    - Power BI Dashboards, tiles, datasets
    - Names, descriptions and URLs of Dashboard and tile
    - Owners of Dashboards

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

    source_config: PowerBiReportServerDashboardSourceConfig
    report: PowerBiReportServerDashboardSourceReport
    accessed_dashboards: int = 0

    def __init__(
        self, config: PowerBiReportServerDashboardSourceConfig, ctx: PipelineContext
    ):
        super().__init__(ctx)
        self.source_config = config
        self.report = PowerBiReportServerDashboardSourceReport()
        self.auth = PowerBiReportServerAPI(self.source_config).get_auth_credentials()
        self.powerbi_client = PowerBiReportServerAPI(self.source_config)
        self.mapper = Mapper(config)
        self.user_dao = UserDao(config)

    @classmethod
    def create(cls, config_dict, ctx):
        config = PowerBiReportServerDashboardSourceConfig.parse_obj(config_dict)
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
                report.UserInfo = self.user_dao.get_owner_by_name(
                    user_name=report.DisplayName
                )
                # if report.HasDataSources:
                #     report.DataSources = self.powerbi_client.get_report_data_sources(report)
            except ValidationError as e:
                message = "Error ({}) occurred while loading User {}(id={})".format(
                    e, report.Name, report.Id
                )
                LOGGER.exception(message, e)
                self.report.report_warning(report.Id, message)
            finally:
                # Increase Dashboard and tiles count in report
                self.report.report_scanned(count=1)
            # Convert PowerBi Report Server Dashboard and child entities
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
