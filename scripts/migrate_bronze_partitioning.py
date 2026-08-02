# scripts/migrate_bronze_partitioning.py
from deltalake import DeltaTable, write_deltalake

from aeropredict.opensky.config import get_delta_root, get_storage_options

delta_root = get_delta_root()
table_uri = f"{delta_root}/bronze/opensky"
opts = get_storage_options()

dt = DeltaTable(table_uri, storage_options=opts)
table = dt.to_pyarrow_table()  # carga todo en memoria - ok si no es gigante

write_deltalake(
    table_uri,
    table,
    partition_by=["ingestion_date"],
    mode="overwrite",
    schema_mode="overwrite",
    storage_options=opts,
)
print(f"Migrados {table.num_rows} rows a partición por ingestion_date")
