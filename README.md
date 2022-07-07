# Introduction 
Ingest PowerBi Report Server metadata to DataHub
Allows to ingest report metadata to DataHub.

Metadata that can be ingested:
   - report name
   - report description
   - ownership(can add existing users in DataHub as owners)
   - transfer folders structure to DataHub as it is in Report Server
   - webUrl to report in Report Server

Due to limits of PBIRS REST API, it's impossible to ingest next data for now:
   - tiles info
   - datasource of report
   - dataset of report

Next types of report can be ingested:
   - PowerBI report(.pbix)
   - Paginated report(.rdl)
   - Mobile report
   - Linked report

# Requirements
DataHub use library orderedset to creating workunits, 
for correct work of module you need:  
   1. Python <=3.8
   2. Microsoft Visual C++ 14.0

# Getting Started
To ingest (.rdl), (.pbix) and mobile reports using this module you need:
1. Install powerbi_report_server module:
         
         pip install git+https://github.com/Temabit-FOZZY-Group/powerbi_report_server_connector

2. Create recipe.yml:

        source:
          type: powerbireportserver.report_server.PowerBiReportServerDashboardSource
          config:
            # Your Power BI Report Server Windows username
            username: username
            # Your Power BI Report Server Windows password
            password: password
            # Your Workstation name
            workstation_name: workstation_name
            # Your Power BI Report Server host URL, example: localhost:80
            host_port: host_port
            # Your alias for Power BI Report Server host URL, example: local_powerbi_report_server
            server_alias: server_alias
            # Workspace's dataset environments, example: (PROD, DEV, QA, STAGE)
            env: DEV
            # Workspace's dataset environments, example: (PROD, DEV, QA, STAGE)
            graphql_url: http://localhost:8080/api/graphql
            # Your Power BI Report Server base virtual directory name for reports
            report_virtual_directory_name: Reports
            #  Your Power BI Report Server base virtual directory name for report server
            report_server_virtual_directory_name: ReportServer
            # dataset_type_mapping is fixed mapping of Power BI datasources type to equivalent Datahub "data platform" dataset
            dataset_type_mapping:
                PostgreSql: postgres
                Oracle: oracle
        sink:
          type: "datahub-rest"
          config:
            server: "http://127.0.0.1:8080"
   



3. Run next command:
   
         datahub ingest -c ./recipe.yml
