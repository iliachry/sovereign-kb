"""Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Architecture Subsystem for Sovereign-KB (`skb soar`)."""

from .ast_nodes import (
    AttrValueTest,
    Condition,
    ConjunctiveTest,
    DisjunctionTest,
    EqualityTest,
    PreferenceSpec,
    Production,
    RelationalTest,
    RHSAction,
    RHSFuncCall,
    RHSFunctionStandalone,
    Var,
)
from .chunk_store import ChunkStore
from .kernel import WME, DecisionPhase, ImpasseInfo, Preference, SoarKernel
from .neuro_symbolic import NeuroSymbolicBridge
from .parser import SoarParser, format_production, parse_soar
from .transpiler import SoarToPythonTranspiler, transpile_soar_to_python
from .vault_agent import (
    CANONICAL_SOAR_BENCHMARKS,
    cmd_soar,
    extract_soar_blocks,
    learned_chunks_path,
    load_vault_productions,
    register_soar_subparser,
    resolve_retrieval_tie_with_soar,
    run_soar_benchmark_suite,
)

__all__ = [
    "AttrValueTest",
    "CANONICAL_SOAR_BENCHMARKS",
    "ChunkStore",
    "Condition",
    "ConjunctiveTest",
    "DecisionPhase",
    "DisjunctionTest",
    "EqualityTest",
    "ImpasseInfo",
    "NeuroSymbolicBridge",
    "Preference",
    "PreferenceSpec",
    "Production",
    "RHSAction",
    "RHSFuncCall",
    "RHSFunctionStandalone",
    "RelationalTest",
    "SoarKernel",
    "SoarParser",
    "SoarToPythonTranspiler",
    "Var",
    "WME",
    "cmd_soar",
    "extract_soar_blocks",
    "format_production",
    "learned_chunks_path",
    "load_vault_productions",
    "parse_soar",
    "register_soar_subparser",
    "resolve_retrieval_tie_with_soar",
    "run_soar_benchmark_suite",
    "transpile_soar_to_python",
]
