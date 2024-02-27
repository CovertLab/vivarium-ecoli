include { run_parca; analysis_parca; create_variants } from './sim'

IMPORTS

workflow {
    run_parca(params.config)
    run_parca.out.toList().set { kb }
    create_variants(params.config, kb)
        .variant_sim_data
        .flatten()
        .set { variant_ch }
WORKFLOW
}
