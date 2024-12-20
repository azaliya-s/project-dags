import os 
import requests
import json
import tempfile
from google.cloud import bigquery
from airflow import DAG
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.operators.python import PythonOperator 
from datetime import datetime, timedelta
from airflow.providers.google.cloud.operators.gcs import GCSHook
from google.cloud import storage, bigquery
from airflow.models import Variable
# from plugins.operators.dbt_operator import DbtRunOperator, DbtTestOperator

# GCP project and dataset details
PROJECT_ID = 'bitcoin-project-444715'
DATASET_RAW = 'raw_dataset'
DATASET_TRANSFORMED = 'transformed_dataset'
TABLE_RAW = 'raw_bitcoin_data'
TABLE_TRANSFORMED = 'bitcoin_cleaned'
TEMP_FILE = '/tmp/bitcoin_data.json'
NDJSON_FILE = '/tmp/bitcoin_data_ndjson.json'
GCS_BUCKET = 'bitcoin-data-bucket'
GCS_PATH = f"raw-data/bitcoin_data_{datetime.now().strftime('%Y-%m-%d')}.json"


default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'start_date': datetime(2024, 12, 6),
}

def fetch_api_data():
    """Fetch data from API and save as JSON file."""
    response = requests.get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1")
    if response.status_code == 200:
        data = response.json()
        with open(TEMP_FILE, 'w') as f:
            json.dump(data['prices'], f)  # Assuming 'prices' is the relevant key
    else:
        raise Exception(f"Failed to fetch data: {response.status_code}")

def convert_to_ndjson():
    """Convert JSON array to NDJSON format."""
    with open(TEMP_FILE, 'r') as infile:
        data = json.load(infile)

    with open(NDJSON_FILE, 'w') as outfile:
        for record in data:
            json.dump({"timestamp": record[0], "price_usd": record[1]}, outfile)
            outfile.write('\n')

# Function to upload data to GCS
def upload_to_gcs():
    service_account_dict = json.loads(Variable.get("google_service_account"))
    temp_sa_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
    json.dump(service_account_dict, temp_sa_file)
    temp_sa_file.close()
    service_account_file = temp_sa_file.name

    try:
        client = storage.Client.from_service_account_json(service_account_file)
        bucket = client.get_bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_PATH)
        blob.upload_from_filename(TEMP_FILE)
        print(f"Uploaded data to GCS: {GCS_BUCKET}/{GCS_PATH}")
    finally:
        os.unlink(service_account_file)

# Function to load data from GCS to BigQuery
def load_to_bigquery():
    service_account_dict = json.loads(Variable.get("google_service_account"))
    temp_sa_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
    json.dump(service_account_dict, temp_sa_file)
    temp_sa_file.close()
    service_account_file = temp_sa_file.name

    try:
        client = bigquery.Client.from_service_account_json(service_account_file)
        table_id = f"{PROJECT_ID}.{DATASET_RAW}.{TABLE_RAW}"
        job_config = bigquery.LoadJobConfig(
            schema=[
                bigquery.SchemaField("timestamp", "INT64"),  # Keep as INT64 for transformation
                bigquery.SchemaField("price_usd", "FLOAT"),
            ],
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        uri = f"gs://{GCS_BUCKET}/{GCS_PATH}"
        load_job = client.load_table_from_uri(uri, table_id, job_config=job_config)
        load_job.result()
        print(f"Loaded data to BigQuery table: {table_id}")
    finally:
        os.unlink(service_account_file)

# Function to transform data in BigQuery
def transform_data():
    service_account_dict = json.loads(Variable.get("google_service_account"))
    temp_sa_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
    json.dump(service_account_dict, temp_sa_file)
    temp_sa_file.close()
    service_account_file = temp_sa_file.name

    try:
        client = bigquery.Client.from_service_account_json(service_account_file)
        query = f"""
        CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_TRANSFORMED}.{TABLE_TRANSFORMED}` AS
        SELECT
            TIMESTAMP_MILLIS(timestamp) AS timestamp,  -- Convert from milliseconds to TIMESTAMP
            price_usd
        FROM `{PROJECT_ID}.{DATASET_RAW}.{TABLE_RAW}`
        WHERE price_usd IS NOT NULL
        """
        query_job = client.query(query)
        query_job.result()
        print(f"Transformed data and created table: {TABLE_TRANSFORMED}")
    finally:
        os.unlink(service_account_file)

# Define the DAG
with DAG(
    dag_id="etl_pipeline_with_gcs_and_bigquery",
    default_args=default_args,
    schedule_interval="@daily",
    catchup=False,
) as dag:

    # Tasks
    extract_task = PythonOperator(
        task_id="fetch_api_data",
        python_callable=fetch_api_data,
    )

    convert_task = PythonOperator(
        task_id="convert_to_ndjson",
        python_callable=convert_to_ndjson,
    )

    upload_task = PythonOperator(
        task_id="upload_to_gcs",
        python_callable=upload_to_gcs,
    )

    load_task = PythonOperator(
        task_id="load_to_bigquery",
        python_callable=load_to_bigquery,
    )

    transform_task = PythonOperator(
        task_id="transform_data",
        python_callable=transform_data,
    )

    # Task dependencies
    extract_task >>  convert_task >>upload_task >> load_task >> transform_task