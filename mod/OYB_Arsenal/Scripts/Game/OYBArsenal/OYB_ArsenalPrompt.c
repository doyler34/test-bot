// Every full arsenal's open prompt reads "OYB Arsenal" instead of the box's own
// name; medical-only ones keep theirs.
modded class SCR_OpenStorageAction
{
	override bool GetActionNameScript(out string outName)
	{
		IEntity owner = GetOwner();
		if (!owner)
			return super.GetActionNameScript(outName);

		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.Cast(owner.FindComponent(SCR_ArsenalComponent));
		if (!arsenal || !arsenal.OYB_IsFull())
			return super.GetActionNameScript(outName);

		outName = "OYB Arsenal";
		return true;
	}
}
