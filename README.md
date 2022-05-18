# Introduction 
Ingest PowerBi Report Server metadata to DataHub

# Getting Started
To ingest data using this module you need: 
    1. Create recipe.yml:
    
    source:
      type: powerbireportserver.report_server.PowerBiReportServerDashboardSource
      config:
        # Your Power BI Report Server Windows username
        username: username
        # Your Power BI Report Server Windows password
        password: password
        # Your Workstation name
        workstation_name: workstation_name
        # Workspace's dataset environments (PROD, DEV, QA, STAGE)
        env: DEV
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

    2. Run next command:
        datahub ingest -c ./recipe.yml
