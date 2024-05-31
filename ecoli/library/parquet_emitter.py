import atexit
import os
import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

import duckdb
import numpy as np
import orjson
import pyarrow as pa
from pyarrow import dataset as ds
from pyarrow import fs
from pyarrow import json as pj
from pyarrow import parquet as pq
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

SCHEMA_HIVE_PARTITION = ("experiment_id=global_parquet_schema/variant=0/"
    "generation=0/agent_id=0")
"""Special Hive partition used to hold unified schema for all cells in an
output directory."""

USE_UINT16 = {
    "listeners__rna_synth_prob__n_bound_TF_per_TU",
    "listeners__rna_synth_prob__n_bound_TF_per_cistron",
    "listeners__rnap_data__rna_init_event_per_cistron",
    "listeners__rna_synth_prob__gene_copy_number",
    "listeners__rna_synth_prob__expected_rna_init_per_cistron",
    "listeners__rna_degradation_listener__count_RNA_degraded_per_cistron",
    "listeners__rna_degradation_listener__count_rna_degraded",
    "listeners__transcript_elongation_listener__count_rna_synthesized",
    "listeners__rnap_data__rna_init_event",
    "listeners__rna_synth_prob__promoter_copy_number",
    "listeners__ribosome_data__n_ribosomes_on_each_mRNA",
    "listeners__ribosome_data__mRNA_TU_index",
    "listeners__complexation_listener__complexation_events",
    "listeners__rnap_data__active_rnap_n_bound_ribosomes",
    "listeners__rnap_data__active_rnap_domain_indexes",
    "listeners__rna_synth_prob__bound_TF_indexes",
    "listeners__rna_synth_prob__bound_TF_domains",
}
"""uint16 is 4x smaller than int64 for values between 0 - 65,535."""

USE_UINT32 = {
    "listeners__ribosome_data__ribosome_init_event_per_monomer",
    "listeners__ribosome_data__n_ribosomes_per_transcript",
    "listeners__rna_counts__partial_mRNA_cistron_counts",
    "listeners__rna_counts__mRNA_cistron_counts",
    "listeners__rna_counts__full_mRNA_cistron_counts",
    "listeners__ribosome_data__n_ribosomes_on_partial_mRNA_per_transcript",
    "listeners__monomer_counts",
    "listeners__rna_counts__partial_mRNA_counts",
    "listeners__rna_counts__mRNA_counts",
    "listeners__rna_counts__full_mRNA_counts",
    "listeners__fba_results__catalyst_counts",
}
"""uint32 is 2x smaller than int64 for values between 0 - 4,294,967,295."""


def json_to_parquet(
    ndjson: str,
    encodings: dict[str, str],
    schema: pa.Schema,
    filesystem: fs.FileSystem,
    outfile: str,
):
    """
    Reads newline-delimited JSON file and converts to Parquet file.

    Args:
        ndjson: Path to newline-delimited JSON file.
        encodings: Mapping of column names to Parquet encodings
        schema: PyArrow schema of Parquet file to write
        filesystem: PyArrow filesystem for Parquet output
        outfile: Filepath of output Parqet file
    """
    parse_options = pj.ParseOptions(explicit_schema=schema)
    read_options = pj.ReadOptions(use_threads=False, block_size=int(1e7))
    t = pj.read_json(ndjson, read_options=read_options, parse_options=parse_options)
    pq.write_table(
        t,
        outfile,
        use_dictionary=False,
        compression="zstd",
        column_encoding=encodings,
        filesystem=filesystem,
    )
    pathlib.Path(ndjson).unlink()


def write_unified_schema(
    schema: pa.Schema,
    filesystem: fs.FileSystem, 
    cell_outdir: str,
    outdir: str
):
    """
    Write a ``_metadata`` file containing unified Parquet schema for current
    cell and create or update global ``_metadata`` which unifies schemas for
    all cells in output directory. The global ``_metadata`` file is written
    to a special Hive partition that should not conflict with any sim output:
    :py:data`~.SCHEMA_HIVE_PARTITION`.


    Args:
        schema: Unified Parquet schema for current cell
        filesystem: PyArrow filesystem for output
        cell_outdir: Sim output directory for current cell (Hive partitioned)
        outdir: Output directory for all cells (history and configuration)
    """
    pq.write_metadata(
        schema,
        os.path.join(cell_outdir, "_metadata"),
        filesystem=filesystem
    )
    for dataset, filename_contains in (
        ("configuration", ".pq"), ("history", ".pq")
    ):
        schemas = [
            pq.read_schema(metadata_file.path, filesystem=filesystem)
            for metadata_file in filesystem.get_file_info(
                fs.FileSelector(os.path.join(outdir, dataset), recursive=True))
            if filename_contains in metadata_file.base_name
        ]
        unified_schema = pa.unify_schemas(schemas)
        unified_schema_dir =  os.path.join(
            outdir, dataset, SCHEMA_HIVE_PARTITION)
        filesystem.create_dir(unified_schema_dir)
        pq.write_metadata(
            unified_schema,
            os.path.join(unified_schema_dir, "_metadata"),
            filesystem=filesystem
        )


def register_sim_views(
    conn: duckdb.DuckDBPyConnection = None,
    out_dir: str = None,
    out_uri: str = None,
):
    """
    Register views of sim configs and outputs in DuckDB connection under
    the names ``configuration`` and ``history``.

    Args:
        conn: DuckDB connection to register views in
        out_dir: Local path to directory containing ``history`` and
            ``configuration`` subdirectories
        out_uri: Remote URI to directory containing ``history`` and
            ``configuration`` subdirectories
    """
    if out_uri is None:
        out_uri = pathlib.Path(out_dir).resolve().as_uri()
    filesystem, out_path = fs.FileSystem.from_uri(out_uri)
    partitioning_schema = pa.schema([
        ("experiment_id", pa.string()),
        ("variant", pa.int32()),
        ("lineage_seed", pa.int32()),
        ("generation", pa.int32()),
        ("agent_id", pa.string())])
    partitioning = ds.partitioning(partitioning_schema, flavor="hive")
    for ds_folder in ["history", "configuration"]:
        ds_out_path = os.path.join(out_path, ds_folder)
        schema = pq.read_schema(
            f'{ds_out_path}/{SCHEMA_HIVE_PARTITION}/_metadata')
        schema = pa.unify_schemas([schema, partitioning_schema])
        # DuckDB's built-in Parquet reader currently does not support
        # Hive partitioning and nested types when specifying a unified
        # schema (see DuckDB PR#9123) and the union_by_name option causes
        # memory usage and evaluation time to explode. Use PyArrow datasets
        # instead because they support unified schemas and are usually just
        # as performant as the built-in reader.
        pa_dataset = ds.dataset(os.path.join(out_path, ds_folder),
            schema=schema, filesystem=filesystem, partitioning=partitioning)
        conn.register(f"unfiltered_{ds_folder}", pa_dataset)


def get_encoding(
    val: Any, field_name: str, use_uint16: bool = False, use_uint32: bool = False
) -> tuple[Any, str]:
    """
    Get optimal PyArrow type and Parquet encoding for input value.
    """
    if isinstance(val, float):
        # Polars does not support BYTE_STREAM_SPLIT yet
        return pa.float64(), "PLAIN", field_name
    elif isinstance(val, bool):
        return pa.bool_(), "RLE", field_name
    elif isinstance(val, int):
        # Optimize memory usage for select integer fields
        if use_uint16:
            pa_type = pa.uint16()
        elif use_uint32:
            pa_type = pa.uint32()
        else:
            pa_type = pa.int64()
        return pa_type, "DELTA_BINARY_PACKED", field_name
    elif isinstance(val, str):
        return pa.string(), "DELTA_BYTE_ARRAY", field_name
    elif isinstance(val, list):
        inner_type, _, field_name = get_encoding(
            val[0], field_name, use_uint16, use_uint32
        )
        # PLAIN encoding yields overall better compressed size for lists
        return pa.list_(inner_type), "PLAIN", field_name + ".list.element"
    raise TypeError(f"{field_name} has unsupported type {type(val)}.")


_FLAG_FIRST = object()


def flatten_dict(d: dict):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the corresponding value
    in the input. Prunes empty dicts and lists. Allows each field in
    emits to be written to, compressed, and encoded as its own column
    in a Parquet file for efficient storage and retrieval.
    """
    results = []

    def visit_key(subdict, results, partialKey):
        for k, v in subdict.items():
            newKey = k if partialKey == _FLAG_FIRST else f"{partialKey}__{k}"
            if isinstance(v, Mapping):
                visit_key(v, results, newKey)
            elif isinstance(v, list) and len(v) == 0:
                continue
            elif isinstance(v, np.ndarray) and len(v) == 0:
                continue
            elif v is None:
                continue
            else:
                results.append((newKey, v))

    visit_key(d, results, _FLAG_FIRST)
    return dict(results)


class ParquetEmitter(Emitter):
    """
    Emit data to a Parquet dataset.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Configure emitter.

        Args:
            config: Should be a dictionary as follows::

                {
                    'type': 'parquet',
                    'config': {
                        'emits_to_batch': Number of emits per Parquet row
                            group (default: 400),
                        # Only one of the following is required
                        'out_dir': absolute or relative output directory,
                        'out_uri': URI of output directory (e.g. s3://...,
                            gs://..., etc), supersedes out_dir
                    }
                }

        """
        out_uri = config["config"].get("out_uri", None)
        if out_uri is None:
            out_uri = (
                pathlib.Path(config["config"].get("out_dir", None)).resolve().as_uri()
            )
        self.filesystem, self.outdir = fs.FileSystem.from_uri(out_uri)
        self.batch_size = config["config"].get("batch_size", 400)
        self.fallback_serializer = make_fallback_serializer_function()
        # Batch emits as newline-delimited JSONs in temporary file
        self.temp_data = tempfile.NamedTemporaryFile(delete=False)
        self.executor = ThreadPoolExecutor(2)
        # Keep a cache of field encodings and fields encountered
        self.encodings = {}
        self.schema = pa.schema([])
        self.num_emits = 0
        atexit.register(self._finalize)

    def _finalize(self):
        """Convert remaining batched emits to Parquet at sim shutdown. Also calls
        :py:func`~.write_unified_schema` to avoid performance penalty of
        schema unification during analysis."""
        if self.num_emits % self.batch_size != 0:
            outfile = os.path.join(
                self.outdir, "history", self.partitioning_path, f"{self.num_emits}.pq"
            )
            json_to_parquet(
                self.temp_data.name,
                self.encodings,
                self.schema,
                self.filesystem,
                outfile,
            )
        write_unified_schema(self.schema, self.filesystem, os.path.join(
            self.outdir, "history", self.partitioning_path), self.outdir)

    def emit(self, data: dict[str, Any]):
        """
        Serializes emit data with ``orjson`` and writes newline-delimited
        JSONs in a temporary file to be batched before conversion to Parquet.

        The output directory consists of two hive-partitioned datasets: one for
        sim metadata called ``configuration`` and another for sim output called
        ``history``. The partitioning keys are, in order, experiment_id (str),
        variant (int), lineage seed (int), generation (int), and agent_id (str).

        By using a single output directory for many runs of a model, advanced
        filtering and computation can be performed on data from all those
        runs using PyArrow datasets (see :py:func:`~.get_datasets`).
        """
        # Config will always be first emit
        if data["table"] == "configuration":
            metadata = data["data"].pop("metadata")
            data["data"] = {**metadata, **data["data"]}
            data["time"] = data["data"].get("initial_global_time", 0.0)
            # Manually create filepaths with hive partitioning
            # Start agent ID with 1 to avoid leading zeros
            agent_id = data["data"].get("agent_id", "1")
            partitioning_keys = {
                "experiment_id": data["data"].get("experiment_id", "default"),
                "variant": data["data"].get("variant", 0),
                "lineage_seed": data["data"].get("lineage_seed", 0),
                "generation": len(agent_id),
                "agent_id": agent_id,
            }
            self.partitioning_path = os.path.join(
                *(f"{k}={v}" for k, v in partitioning_keys.items())
            )
            data = flatten_dict(data)
            data_str = orjson.dumps(
                data,
                option=orjson.OPT_SERIALIZE_NUMPY,
                default=self.fallback_serializer,
            )
            self.temp_data.write(data_str)
            data = orjson.loads(data_str)
            encodings = {}
            schema = []
            for k, v in data.items():
                pa_type, encoding, field_name = get_encoding(v, k)
                if encoding is not None:
                    encodings[field_name] = encoding
                schema.append((k, pa_type))
            outfile = os.path.join(
                self.outdir, data["table"], self.partitioning_path, "config.pq"
            )
            # Cleanup any existing output files from previous runs then
            # create new folder for config / simulation output
            try:
                self.filesystem.delete_dir(os.path.dirname(outfile))
            except FileNotFoundError:
                pass
            self.filesystem.create_dir(os.path.dirname(outfile))
            history_outdir = os.path.join(
                self.outdir, "history", self.partitioning_path
            )
            try:
                self.filesystem.delete_dir(history_outdir)
            except FileNotFoundError:
                pass
            self.filesystem.create_dir(history_outdir)
            self.executor.submit(
                json_to_parquet,
                self.temp_data.name,
                self.encodings,
                pa.schema(schema),
                self.filesystem,
                outfile,
            )
            self.temp_data = tempfile.NamedTemporaryFile(delete=False)
            return
        # Each Engine that uses this emitter should only simulate a single cell
        # In lineage simulations, StopAfterDivision Step will terminate
        # Engine in timestep immediately after division (first with 2 cells)
        # In colony simulations, EngineProcess will terminate simulation
        # immediately upon division (following branch is never invoked)
        if len(data["data"]["agents"]) > 1:
            return
        for agent_data in data["data"]["agents"].values():
            agent_data["time"] = float(data["data"]["time"])
            agent_data = flatten_dict(agent_data)
            agent_data_str = orjson.dumps(
                agent_data,
                option=orjson.OPT_SERIALIZE_NUMPY,
                default=self.fallback_serializer,
            )
            self.temp_data.write(agent_data_str)
            self.temp_data.write("\n".encode("utf-8"))
            new_keys = set(agent_data) - set(self.schema.names)
            if len(new_keys) > 0:
                agent_data = orjson.loads(agent_data_str)
                for k in new_keys:
                    pa_type, encoding, field_name = get_encoding(
                        agent_data[k], k, k in USE_UINT16, k in USE_UINT32
                    )
                    if encoding is not None:
                        self.encodings[field_name] = encoding
                    self.schema = self.schema.append(pa.field(k, pa_type))
        self.num_emits += 1
        if self.num_emits % self.batch_size == 0:
            self.temp_data.close()
            outfile = os.path.join(
                self.outdir,
                data["table"],
                self.partitioning_path,
                f"{self.num_emits}.pq",
            )
            self.executor.submit(
                json_to_parquet,
                self.temp_data.name,
                self.encodings,
                self.schema,
                self.filesystem,
                outfile,
            )
            self.temp_data = tempfile.NamedTemporaryFile(delete=False)
