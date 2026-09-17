/**
 * @NApiVersion 2.1
 * @NScriptType ScheduledScript
 */
define(['N/record', 'N/runtime', './lib'], (record, runtime, lib) => {
    function execute() {
        const days = Math.min(90, Math.max(1, Number(lib.param('custscript_nsa_retention_days')) || 7));
        const rows = lib.list([[lib.F.created, 'lessthan', Date.now() - days * 86400000]], 1000);
        for (const row of rows) {
            if (runtime.getCurrentScript().getRemainingUsage() < 100) break;
            record.delete({type: lib.TYPE, id: row.id});
        }
    }
    return {execute};
});
