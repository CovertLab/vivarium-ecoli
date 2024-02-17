process sim_gen_0 {
    errorStrategy 'ignore'

    tag "$sim_data"

    input:
    path config
    path sim_data
    val seed

    output:
    path config, emit: config
    path sim_data, emit: sim_data
    val seed, emit: initial_seed
    val generation, emit: generation
    path 'daughter_state_0.json', emit: d1
    path 'daughter_state_1.json', emit: d2
    tuple path(sim_data), val(seed), path('mongodb'), emit: db

    script:
    """
    python ${params.project_root}/ecoli/experiments/ecoli_master_sim.py -c $config --sim_data_path $sim_data
    """

    stub:
    """
    mkdir mongodb
    echo "$config $sim_data $initial_seed $generation $initial_state $sim_seed" > mongodb/fake.data
    touch daughter_state_0.json
    touch daughter_state_1.json
    """
}
