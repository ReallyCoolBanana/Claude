# Module Inventory -- storage/coordination/*.py

Generated: 2026-03-11 by Research Team 3A

---

## bus_core.py
- **Description:** Canonical shared primitives for the Proto A coordination bus and SQLite layer
- **Classes:** (none)
- **Key functions:** sanitize_channel, is_busy_or_locked, retry_on_busy, init_db, bus_write, bus_read
- **Internal imports:** (none -- this is the foundational module)

## bus_cli.py
- **Description:** CLI tool for agents to read/write the Proto A JSONL message bus
- **Classes:** (none)
- **Key functions:** write_msg, read_msgs, init_db, register_agent, post_finding, get_all_findings, post_proposal, get_status
- **Internal imports:** (none directly at top; uses own BUS_DIR/DB_DIR paths)

## coordinator_hub.py
- **Description:** Hub-and-spoke agent monitoring via shared SQLite status table
- **Classes:** AgentReporter, CoordinatorDashboard
- **Key functions:** _retry_on_busy, _open_db, _bus_notify
- **Internal imports:** (none -- standalone, defines own retry/db helpers)

## dashboard.py
- **Description:** Text-based real-time dashboard for the Proto A coordination system
- **Classes:** _SharedDB
- **Key functions:** _get_agents, _get_phase_signals, _get_findings_summary, _get_runner_state, _get_bus_activity, render_header, render_agents, render_bus_activity, render_team_completion, render_findings, render_dashboard, main
- **Internal imports:** (none -- reads db/bus files directly)

## db_utils.py
- **Description:** Common database utilities -- connection management, schema creation, atomic transactions
- **Classes:** (none)
- **Key functions:** get_connection, ensure_schema, atomic_update, safe_close
- **Internal imports:** (none -- standalone utility module)

## direct_channels.py
- **Description:** Named team-to-team channels, presence tracking, and progress broadcasting
- **Classes:** DirectChannels
- **Key functions:** _retry_on_busy, _direct_channel_name, _safe_channel, _bus_publish, _bus_read
- **Internal imports:** (none -- defines own retry/bus helpers)

## help_protocol.py
- **Description:** Team help protocol -- work-item tracking, help-request/offer/accept, idle detection
- **Classes:** HelpProtocol
- **Key functions:** _is_busy_or_locked, _retry_on_busy
- **Internal imports:** (none -- defines own retry helpers)

## multi_team_runner.py
- **Description:** Production multi-team launcher with heartbeat monitoring and clean shutdown
- **Classes:** MultiTeamRunner
- **Key functions:** _setup_logging, _bus_write, _get_connection, _init_db, _register_agent, _heartbeat, _get_dead_agents, _set_runner_state, _load_config, _signal_handler, _print_status, _request_shutdown, main
- **Internal imports:** (none -- defines own db/bus helpers)

## think_tank.py
- **Description:** Cross-team findings convergence -- deduplicates, groups, and generates improvement roadmap
- **Classes:** (none)
- **Key functions:** _priority_key, _fingerprint, _normalise_finding, _collect_from_sqlite, _collect_from_files, _deduplicate, _group_findings, _generate_roadmap, _suggest_action, generate_report, write_report, print_summary, main
- **Internal imports:** (none -- reads db/files directly)

## work_stealing.py
- **Description:** Work stealing queues, pipeline chaining, and shared scratchpad on SQLite WAL
- **Classes:** WorkStealing, PipelineManager, Scratchpad
- **Key functions:** _retry_on_busy, _open_db, _bus_notify
- **Internal imports:** (none -- defines own retry/db/bus helpers)

## sdk_config.py
- **Description:** YAML/JSON-based configuration system for multi-agent team launches
- **Classes:** AgentRole (Enum), TeamDefinition (dataclass), LaunchConfig (dataclass)
- **Key functions:** load_config, load_named_config, validate_config, standard_dev_team, research_team, review_team, full_production_team, get_preset
- **Internal imports:** (none -- standalone config module)

---

## Cross-cutting observations

- **Duplicated patterns:** retry_on_busy, _open_db/_init_db, and bus write/read helpers are independently defined in coordinator_hub, direct_channels, help_protocol, work_stealing, multi_team_runner, and bus_cli. bus_core.py was created to consolidate these but most modules have NOT migrated to use it yet.
- **db_utils.py** provides get_connection/ensure_schema/atomic_update but is also not imported by any other module in this directory.
- **No module imports from another coordination module** at the top level -- each is self-contained with its own copies of common patterns.
- **Total: 11 Python modules, 10 classes, ~80 functions.**
