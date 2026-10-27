// Every arsenal's open prompt reads "OYB Arsenal" instead of the box's own name.
modded class SCR_OpenStorageAction
{
	override bool GetActionNameScript(out string outName)
	{
		IEntity owner = GetOwner();
		if (!owner || !owner.FindComponent(SCR_ArsenalComponent))
			return super.GetActionNameScript(outName);

		outName = "OYB Arsenal";
		return true;
	}
}
