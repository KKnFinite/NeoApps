# NeoSektor shared authority contract

The existing NeoSektor operational worksheet remains the legacy compatibility
surface.  Its operational cells (including `D2` and `D3`) must never be used as
an authority flag.

When both NeoApps and the standalone NeoSektor backup have been upgraded, set
`GOOGLE_SHEETS_NEOSEKTOR_AUTHORITY_TAB` in both deployments to the same
dedicated, access-restricted Google worksheet.  The worksheet has one record:

| Cell | Value |
| --- | --- |
| `A2` | `neo_primary` or `standalone_primary` |
| `B2` | monotonically increasing generation |
| `C2` | UTC transition timestamp |
| `D2` | actor |
| `E2` | JSON metadata reserved for reconciliation details |

The tab should be protected in the workbook so only authorized control users
and the two service identities can change it.  NeoApps does not provision this
record automatically: without the environment setting and initialized record,
both applications retain their legacy compatibility behavior.

Google Sheets does not expose a true compare-and-swap operation through the
current integration.  A transition therefore uses read → expected-generation
check → write next generation → forced re-read verification.  Both clients
must use that same protocol, stop operational writes before taking control, and
fail closed if an active record cannot be read.  The verification reduces but
does not eliminate a simultaneous-writer race; operational process and the
protected control tab remain required until a stronger shared coordinator is
available.
