const test=require('node:test');
const assert=require('node:assert/strict');
const {directReceipt}=require('../../app/static/js/neoscorpion_fuel_data.js');
test('direct entry needs an authoritative committed receipt, never HTTP 200 alone',()=>{
    assert.throws(()=>directReceipt({ok:true},{ok:true}),/not confirmed/);
    assert.throws(()=>directReceipt({ok:true},{ok:false,error:'Invalid Actual'}),/Invalid Actual/);
    assert.throws(()=>directReceipt({ok:false,status:409},{ok:false,error:'Mission changed'}),/Mission changed/);
    const payload={ok:true,direct_entry_receipt:{id:12,completed_at:'2026-10-22T02:00:00'}};
    assert.equal(directReceipt({ok:true},payload),payload);
});
