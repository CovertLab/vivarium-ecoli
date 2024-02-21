process sim {
    errorStrategy 'ignore'

    tag "$sim_data"

    input:
    path config
    tuple path(sim_data), val(initial_seed), val(generation)
    path initial_state
    val sim_seed
    val cell_id

    output:
    path config, emit: config
    tuple path(sim_data), val(initial_seed), val(generation+1), emit: next_gen
    path 'daughter_state_0.json', emit: d1
    path 'daughter_state_1.json', emit: d2
    tuple path(sim_data), val(initial_seed), val(generation), val(cell_id), emit: metadata

    script:
    """
    # Create daughter states so workflow can continue
    touch daughter_state_0.json
    touch daughter_state_1.json
    python ${params.project_root}/ecoli/experiments/ecoli_master_sim.py \
        -c $config \
        --sim_data_path $sim_data \
        --initial_state_file $initial_state \
        --daughter_outdir \$(pwd)
    """

    stub:
    """
    echo "$config $sim_data $initial_seed $generation" > daughter_state_0.json
    echo "$initial_state $sim_seed" > daughter_state_1.json
    """
}
