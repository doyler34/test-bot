# Deploy OYB alongside existing Reforger servers

Run `bash deploy/setup.sh` from this branch on the actual VPS.

See [the production setup guide](../deploy/README.md) for the exact flow,
requirements, log discovery, state preservation and `oyb` management commands.

The installer is bot-only. The former combined test-game installer is retired;
it no longer installs Reforger or modifies game configs, saves, profiles or ports.
