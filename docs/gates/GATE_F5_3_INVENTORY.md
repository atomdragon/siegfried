# Gate F5.3 — inventario y recuperación Git

Baseline inicial limpio: `c9a20e4235cc9ede4040f3a3b4a4379c8f26b565`. 108 archivos rastreados; 0 modificados, 0 staged, 0 sin seguimiento al iniciar F5.3. El commit previo fue realizado antes de este Gate; el agente no ejecutó commits.

## Revisión de inclusión accidental

Sin archivos >1 MiB, modelos GGUF/safetensors, secrets.env ni JSONL de usuario en el inventario rastreado. El escaneo de patrones de clave privada/token largo sólo coincidió con fixtures sintéticas en `tests/integration/test_alerts_kde_audio_f32.py` y `tests/unit/test_cloud_inference.py`. No se imprime el contenido de esas coincidencias. Es un escaneo acotado del repositorio, no una garantía de ausencia de todo secreto posible.

## Propuesta humana de recuperación

Antes de cualquier staging, revisar el diff final, archivos nuevos y el reporte F5.3. Propuesta de commit para autorización humana posterior: `feat: complete Gate F5.3 deterministic KDE boot briefing`. Incluir sólo fuentes, tests, herramientas y documentación enumeradas en el reporte; excluir HOME, datos, secretos, modelos y artefactos temporales. No se ejecutaron git add, commit ni push. No hace falta capturar de nuevo las fases previas: ya estaban en HEAD.

## Manifest del baseline (SHA-256 de blobs Git, no datos del HOME)

| Archivo | Bytes | SHA-256 |
|---|---:|---|
| `.gitignore` | 466 | `d8779156b7421d34437a62752bc32c59267a44669cc884f2304bd74e4a70a380` |
| `PLAN.md` | 53319 | `6ebed4893bfbac9022302bbc50950558d89e82110cbfb02eb4608972bc4fc73d` |
| `README.md` | 26640 | `6abfe9d984300517033b5ef81643a1e1ab5cc28cbd9bd7c25f58ad8d77fe0585` |
| `SPECIFICATION.md` | 17812 | `831720f228583496b299d478cffda967135c3288022e72d025cedd38103f0049` |
| `bin/siegfried` | 437 | `e1fd543128317712fa9cbb8c44ec210436b94a4b9d7fce39e933e56174b03bbc` |
| `bin/siegfried-daemon` | 909 | `ebc289a396c3f564a4634211b2999c58deca4fc2fd67c397a17c0fba46ee779b` |
| `docs/gates/GATE_F2_5_REPORT.md` | 15926 | `5c0ca84fda9dced24e3999b1a4f248938e7451a120967069f80fa22e80fe71b4` |
| `docs/gates/GATE_F3_1_REPORT.md` | 9177 | `5836124e04a0cc70115411003c46e39bf45389195f3d6a887d9086809ea66f53` |
| `docs/gates/GATE_F3_2_REPORT.md` | 12938 | `d367aba8efd5a7c4d0101cd2022e25e380c7ce98aa20f524c516c5fd8d6838ab` |
| `docs/gates/GATE_F3_3_REPORT.md` | 14057 | `139491d51a5d15b77a67a85286eabb6a63ab0ba2a3649267ed64bd7ae6151a6c` |
| `docs/gates/GATE_F4_1_REPORT.md` | 19061 | `8f8608478afefa74e297c26c00b1af7e69b52d3258626f35fedcc3eaa456a9f1` |
| `docs/gates/GATE_F4_2_HARDENING_REPORT.md` | 14926 | `3866518a2439d92c8d6247ef86e94287447bfe97191a8c5bcbc157b685be6498` |
| `docs/gates/GATE_F4_2_REPORT.md` | 15293 | `90cbcbc0b945f840326f03d7c3689e1ee496bc0bddec8b5cfef9793fc4b3f58a` |
| `docs/gates/GATE_F5_1_1_REPORT.md` | 19990 | `3f50045468a227189cb7e74f95cd4d343225aab0508fe7d781903d2966c14695` |
| `docs/gates/GATE_F5_1_REPORT.md` | 22006 | `5594c76774458835bc82990ba829eeaef56f53cd701c32335478578ccb4a3cc6` |
| `docs/gates/GATE_F5_2_REPORT.md` | 30314 | `62301fe2b31578ded4151c889a2f3fd83bdec11503624ed7def68bcfb610da7e` |
| `schemas/config-v1.schema.json` | 1412 | `d70c35ec123a615604a7415d9771a2b07e40b7cc731ae12d59f81292f5352425` |
| `schemas/event-v1.schema.json` | 1062 | `499ca1c2ac4bc8019d39490c1561efd2f6fd4554c3497758e6c450fb61fb14ce` |
| `schemas/ipc-v1.schema.json` | 1111 | `a1bdfa19821a721b733bf4b74b75e3bb27620ec93ad5b81184cf8a3a5860f163` |
| `scripts/boot_hook.py` | 1859 | `13c18b8a311ef109661374b361cb8850f2da097bfb3c66dfff9f89269fb37c08` |
| `scripts/kwin_focus_watcher.js` | 4140 | `0230a49aac6ced09cabe3040274adf30dbdfee3a501bc9a7e4cfa2f7508847cb` |
| `scripts/kwin_focus_watcher/contents/code/main.js` | 4140 | `0230a49aac6ced09cabe3040274adf30dbdfee3a501bc9a7e4cfa2f7508847cb` |
| `scripts/kwin_focus_watcher/metadata.json` | 509 | `0f7476fddf2625d9e76fba6445ea92905c1f383fb393f4027e1cc0586a4bbb97` |
| `src/siegfried/__init__.py` | 164 | `542682b36b010ac923bc481356cc100e17fe123447f339e262047cc6e340946c` |
| `src/siegfried/cli/__init__.py` | 702 | `e2541bf0d09e2c5138f9fc2c8554c9d59cd92a4f0fba59ba99ffcc4529dbdc12` |
| `src/siegfried/cli/app.py` | 9519 | `f459e240655b2c7713eaf062536a2c5d1cc54c6cc03d97ac811ba97c9dbbd96a` |
| `src/siegfried/cli/repl.py` | 12105 | `ac18b560d5e54f3099ebb42b400fb3602423f38e9664e4bc62492baccb03d40b` |
| `src/siegfried/cli/router.py` | 7412 | `1e77b16b8b4e4a683f6296a408647ebd91629f0e9d8b55142945f5ffc1fdcb3d` |
| `src/siegfried/contracts/__init__.py` | 1601 | `ce1e0966ef66db5feaa48ef0425ee10f664ae34f3b23c514ba6f4b0fb0a9dfae` |
| `src/siegfried/contracts/alerts.py` | 1694 | `72b564e42403cb60122b14ac4dd67a36e93463ced84d904d6263373b1548dab6` |
| `src/siegfried/contracts/config.py` | 4118 | `6d169ba5a862c06b4265f60b70c2a8f5d459a9c90b8acf9faa2b1aace07a2752` |
| `src/siegfried/contracts/events.py` | 2827 | `0a1c13c6d21e72dce114df745dbdcb6b863edd2c0d7a07145c8e3de618a561fa` |
| `src/siegfried/contracts/inference.py` | 6746 | `b9d90c8f35dde4937d56c6ac53149af6b658504f89a1656aaaa79f6dd063c249` |
| `src/siegfried/contracts/ipc.py` | 5025 | `624cba90e541358fa46d223224fb5d2be938e2be841f269b9051672c04bcd16a` |
| `src/siegfried/contracts/states.py` | 697 | `06018e39028463b7afd9a9c2e8728cfa999a29d583a298be18f6425e0d92ae98` |
| `src/siegfried/core/__init__.py` | 614 | `15cab3c77e0a765d1cb499acb4b93bb9b17598e0efe7efd059c0d03397b2797a` |
| `src/siegfried/core/clock.py` | 807 | `6d7eb83145d5140f8c42157f55cfaab3691c352f36829b77102ad02493869f3f` |
| `src/siegfried/core/errors.py` | 6012 | `72c6238bf43911bbc22c9b4d7baf57ed952fdeca37cd11f351de578f39b55581` |
| `src/siegfried/core/rest.py` | 4358 | `537d20b1ffd7f0599822e4726c342d1bbe66eb84af9f743c29f4e644158b1dd6` |
| `src/siegfried/core/state_machine.py` | 4051 | `8ad7a19f8f79f3b4dbdc1cd9184095f0b4d823702e526a5e1381970414bc025a` |
| `src/siegfried/daemon/__init__.py` | 466 | `d8724fa8a5d1af6024d825baee2c45f2320540bd1e6797f4d24ebc4dab62906f` |
| `src/siegfried/daemon/__main__.py` | 702 | `d0384ffcb933217eb0a65de9b7fb3463375d9bfd00e8b558157103daf8d6a7b9` |
| `src/siegfried/daemon/alerts.py` | 19827 | `1996ca589893ef60c19f014107060d5f74fe5161a4cc96cc85db6fabc1c9e827` |
| `src/siegfried/daemon/app.py` | 29809 | `affa969c9d88b4838ed3b4e25cd01270ca86f3a348c5ef61614085daaa81921e` |
| `src/siegfried/daemon/focus.py` | 23955 | `7f3865993c5b61b1b0ee1e9f13e96c21ed67c7561379fcc0bdc93698cfad98af` |
| `src/siegfried/daemon/session.py` | 7250 | `84439e0ac9ee7552031b77cb4ca3d6918f3dd448a8ef3c2e466c3b82bc395485` |
| `src/siegfried/daemon/timers.py` | 2441 | `bb474e04f2dc72c24e1340c8656847387fc501f04286a4b750b8017d50ec5fad` |
| `src/siegfried/inference/__init__.py` | 1420 | `04b6a19baa8f4fd55509d2807f0c5e459859a025c4bf0e354a2215fb8b343a07` |
| `src/siegfried/inference/cloud.py` | 18531 | `1bb56503b7038295bd6d626260412cbc4934ef1315710f2299683e7889c4897e` |
| `src/siegfried/inference/llama_manager.py` | 18751 | `1399ade1999c7fe2c87a82793a8c0977a6b757d5511a94982601ccab51f1041a` |
| `src/siegfried/inference/local.py` | 11624 | `d12746b7d33c83d0b03c2bac9fc90496b52a779d9ce51de8a0f3f317bf076183` |
| `src/siegfried/inference/orchestrator.py` | 18866 | `1826f7932fa53e6390a3a21e828551c99a196abdfbe243f4f510e8a2dc49064d` |
| `src/siegfried/inference/resources.py` | 7408 | `7820396e0d8ae672153de93a751fcb1ff4d1eaabe6ee89eab816afc8076bb7a8` |
| `src/siegfried/integrations/__init__.py` | 649 | `39ee1d1643583097cd54a6be24d645245093fc3172b8575d64981f840b23e26d` |
| `src/siegfried/integrations/audio.py` | 6748 | `f00edf5596da3f138d70cdd79314e962e64fbfe7653c2af7859346cbf6e1416a` |
| `src/siegfried/integrations/kwin.py` | 1574 | `be1782cf7fe797dfd618b017ee5a2bad590a7fbad6e7d3c212b51fda3e3fdf08` |
| `src/siegfried/integrations/notifications.py` | 5032 | `7cbaf69b2b45dadb65e5a7e6e40b34b13192241207266829ece194f35718d02c` |
| `src/siegfried/integrations/session.py` | 17324 | `f78b73b9112bf15a8dfb687378956c8203d97ff04620acd587a9f724d4ff3d62` |
| `src/siegfried/ipc/__init__.py` | 369 | `35451e31c5504bd956047c03430395b73dbe2e160f13cc894a42d83906b4a137` |
| `src/siegfried/ipc/client.py` | 2437 | `59d17fe70a04cb66241704ef97b3b2df2e10e8d014da3acd9cab6ca899203817` |
| `src/siegfried/ipc/protocol.py` | 1511 | `b3e8c9b6f1b5555e66f6d350001c14f3b97dd204b74b479037ccf4a6c9223d36` |
| `src/siegfried/ipc/server.py` | 18465 | `c98a7968ec9b0aff171a231993cfe34da8d1a5ef100b9ebe3758a57691014286` |
| `src/siegfried/observability/__init__.py` | 418 | `1f4229f97890b211ecf38e88cbefff939fcdd287cfcc8b82620f7706934eb09e` |
| `src/siegfried/observability/benchmarks.py` | 1366 | `e4d5102db1a5b2546f051e992d40203b97103c807aa439f4a4f917e883188cbb` |
| `src/siegfried/observability/logging.py` | 3132 | `9afc1e14ad70497cca9460c8693be4c66332bb961f5beaba53158ba7306867e8` |
| `src/siegfried/storage/__init__.py` | 1149 | `8c7c1c1518aeb6c71db4fabfc205ecbfe43996de8ebd0c59d960132733dbc55a` |
| `src/siegfried/storage/aggregator.py` | 13427 | `304f32b78721583cb0004a90d36e9f9b4c66ea7f03b6df9db236ac518e4533cb` |
| `src/siegfried/storage/atomic_json.py` | 4937 | `30472d2e49cea23566a02dde537ab565435b1e4563212b9c6745ba32feb6f9fe` |
| `src/siegfried/storage/initialization.py` | 13419 | `e4c5c34d9afb156b18ca460275684ead8616e06987d5578476869c16702c8407` |
| `src/siegfried/storage/paths.py` | 3702 | `9e9fa08e3ef0328486071bc610c83d307a4b4f3dfdcf5dd88da34640aa5ea1e2` |
| `src/siegfried/storage/secrets.py` | 4589 | `165d4f6e350783d3ac68fae4c771e6dceb0822bff09c1ddce0ca86448e093dd9` |
| `src/siegfried/storage/validation.py` | 17288 | `ba8c82bdf5889585a6ff68a2137eaf3527b02867199776ace2444e188da924e4` |
| `src/siegfried/storage/vault.py` | 12187 | `3c6e3a0acde8a194457ea87a41c80d257c572dee8b2bbe037fbffe84b41379e0` |
| `systemd/siegfried.service` | 607 | `554189fec5fc434ffd535870679470bd9757bfc24daf97a1c0b32bebaeadf529` |
| `tests/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/integration/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/integration/test_alert_reliability_f33.py` | 45327 | `5c0ddd4b1c8d79d057c778d6745a0e2a9902bc381c62efc0bf899d68549c8f79` |
| `tests/integration/test_alerts_kde_audio_f32.py` | 31334 | `9ccab4169557b2463e942cde5f316962753576c59e97cb7ff5553ff7ca59d40a` |
| `tests/integration/test_crash_recovery.py` | 9080 | `b1b7a4e2c603d226125125492a79f22e81915a879672ce141541f5b202bd4c0d` |
| `tests/integration/test_daemon_timer_cli_death.py` | 6463 | `6ec83307d58668dc8d5242a5d5cef240e3250ed8a1e84cf7955451837280988a` |
| `tests/integration/test_doctor_and_daemon_validation.py` | 6451 | `74b51746c754777e4937fbdb1d64c200c697edbdfb0f75bf45c3694429340669` |
| `tests/integration/test_e2e_real_processes_f31.py` | 7824 | `4429f70e9f0551218110b5059338fbcafd4f50fc979ca939c8c0745b9e392cd4` |
| `tests/integration/test_functional_integration_f24.py` | 27128 | `67f01d9ad24a94cdacf6f4ae9cd3bb475d1bf77cccfcfca3c3351543603de14a` |
| `tests/integration/test_gate_f31_daemon_systemd.py` | 17718 | `9d88b4ae7a186a6bb6c080f39512ca68381eee68d60cb9c4c14c08a0e6f1651d` |
| `tests/integration/test_historical_inference_f42.py` | 31260 | `b60d7cd25a88c242901ddb27496ba113824b85e25e4e9c1b69007b707271b961` |
| `tests/integration/test_init_scenarios.py` | 5266 | `edfbc7793c48121d5eec480249b7958fd0d966463dbc446bf4fa80cbc5c46ca5` |
| `tests/integration/test_ipc_backpressure_f241.py` | 33543 | `6bc992b4f6669431edf3c3562c772bd7549170bf4d9136b0fe34a92a7541bb05` |
| `tests/integration/test_kwin_focus_f51.py` | 26117 | `57d3b12cb2fb81925826bbd5690557d3486a4e8260b405c6c6d9390bdfb7df21` |
| `tests/integration/test_kwin_focus_f511.py` | 8188 | `a1b0a8f7de01a661c1b0beb4a5cbf5fe00872212cf54657e5df0307ef94b34fa` |
| `tests/integration/test_m01_gate.py` | 33287 | `60315b83fc380f00fce51ae73bdd4e4a195408addcd5d72e3f6adc4eef10b900` |
| `tests/integration/test_m0_vertical_slice.py` | 4276 | `9b9fa0f9f458f5d1c76fc9db638b0fd598e4b09c20a1267e5674eff41c3fa48a` |
| `tests/integration/test_repl_interactive_f41.py` | 38860 | `7dcdb0e7a8ea5cf1d8b95e0fc79f83e50ff5ec7232e6e085f600f366d3f126c5` |
| `tests/integration/test_session_rest_f52.py` | 36953 | `bddc43aabde14a126c22f211a1f673731c73fd9b1981bf17211ef4c541ef7c0a` |
| `tests/unit/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/unit/test_cloud_inference.py` | 16977 | `dba33a31ed31a89d7eb3db2277f1a0b44eb2aaa67c3b28ad260dda225c5c55de` |
| `tests/unit/test_contracts.py` | 4795 | `d40f0166a0eceec9baca90e38386529a6b76ce621e0f9f1827020224c637bd10` |
| `tests/unit/test_initialization.py` | 17684 | `54e3f7a35aa33347a64117530f797a220ebba3f839ff5cf5a90ccdf6886af900` |
| `tests/unit/test_local_inference.py` | 26144 | `68fda3af209adfb7e1f30979af854b911df531c7cb77cbc36c6833013d3a05ab` |
| `tests/unit/test_orchestrator.py` | 37049 | `37bbc4ad5953d2f774dcba338f16a8ac9920d79924554d1bd5414fa43f8ef578` |
| `tests/unit/test_persistence_faults.py` | 15522 | `604ac7984d3cd8eba98c9626f0ad272fdcf0a7be710aa7aaa2f8585740077ce2` |
| `tests/unit/test_secrets.py` | 3245 | `f79f37a419a5c6f6039f858efbb737080314461a49070020b92a4ebd232a8e2c` |
| `tests/unit/test_state_machine.py` | 2326 | `cb9ba3136c4893fc4c71f12e04a673d364b40b5a769930b08a1ca1fa447f42b5` |
| `tests/unit/test_storage.py` | 2577 | `031ec9033e23d010c632d4c0c161588cd82a0ece3979f5e29c156fb470a0c3bf` |
| `tests/unit/test_validation.py` | 12690 | `11fd6ed83aa70973f750743ec8dc8052f045d53001851765dc0fce9cc8cf0211` |
| `tools/benchmark.py` | 9358 | `d345b43e23ea6817b98611bcf47670cd39ede3fdb22ff3b7c0ac26fd2b5e6e5a` |
| `tools/install_user_service.py` | 4545 | `04d2a47591231acfa70d00041a3941d595a888ef749ac1e10a7426b7c7402a2f` |
| `tools/verify_kwin_f511.py` | 9947 | `bcd180e45d539ca874d21ff89368ac12540fd72dbbc42e335e1d722a2f363c6a` |
| `tools/verify_session_f52.py` | 10966 | `e619e2cc055c67a08e5ff55612b77e73d15ce1cc9f28e0ff5019823d31df594e` |

## Inventario de cambios F5.3 para revisión humana

5 rastreados modificados y 11 nuevos; HEAD y staging preservados. Hashes del contenido final, salvo este documento autorreferente. Los nuevos archivos fueron revisados: código, herramientas, pruebas y documentación; sin modelos, credenciales ni datos personales.

| Estado | Archivo | Bytes | SHA-256 |
|---|---|---:|---|
| modificado | `PLAN.md` | 55815 | `0c3633fa86b6c4bd659817f7e0973795abc6b6d7031b788bdf3431e79c30d4d7` |
| modificado | `README.md` | 30185 | `4193b7f278dc11ccccd632ee8dc083456221a77baedabdaf620bb93f0fc0e206` |
| modificado | `SPECIFICATION.md` | 20036 | `9f06e4b9b53b80ab3c890b21f67be2804bdb8ea01d13dec79a57981542ba93a8` |
| nuevo | `docs/gates/GATE_F5_3_INVENTORY.md` | — | excluido por autorreferencia |
| nuevo | `docs/gates/GATE_F5_3_REPORT.md` | 24428 | `e2c1375c2e6e1212fd9548f41e35a101035fb3de87eec472c42409e1bce77181` |
| modificado | `scripts/boot_hook.py` | 3719 | `2bc7c432a7b7341832ce5260ef65ff5337fcd741c34826134760801de698c6e2` |
| nuevo | `src/siegfried/core/briefing.py` | 3025 | `d6f0c607245b25b3a59a4586ae6f759494bc667b635e3ae1d7e7471a9f088b47` |
| nuevo | `src/siegfried/integrations/boot_briefing.py` | 5928 | `2ff6c8fbadaef0859efad6cb8db88b7b31f0da8dc3a30a80cde8ba42f81556e8` |
| nuevo | `src/siegfried/integrations/briefing_files.py` | 4388 | `7915705ff51fc9fe1a723c90c919e560a1807df929d6bfbaf37723d091a67579` |
| nuevo | `src/siegfried/integrations/briefing_kde.py` | 11794 | `29151f269e83b521a3d10b8df5d9612f996897ffdeb66d1000055829316fb0b5` |
| nuevo | `src/siegfried/integrations/weather.py` | 2871 | `bac744faf66f607ac7cf1e66b22f863366a878e266074f8965a82231750d3f3d` |
| modificado | `src/siegfried/storage/paths.py` | 3821 | `1a01632efb60c2b3af60018d8701efb93c9891b863cef068e2662d06531332c1` |
| nuevo | `tests/integration/test_boot_briefing_f53.py` | 26179 | `a3ba1353c5c47d31273872b3c3d64ced7bc9b8882fe3685af3aebb18ad0a9dcb` |
| nuevo | `tools/benchmark_boot_briefing.py` | 1448 | `ae3d6e7d4c1d4a1a46210aecec05b3521f43b3d1a4d2a0177fb7abcdb1c8e9b3` |
| nuevo | `tools/install_boot_briefing.py` | 12173 | `799456efbc32ee151ac4fd5724f476a2fb5ee5107fd7e931b3bab60011b8a834` |
| nuevo | `tools/verify_boot_briefing_f53.py` | 3012 | `f223f47a51ab79eb37191f72b839257996f2109736951b9091b4a25919c44056` |
