// Every mortar round in the game carries SCR_MortarShellGadgetComponent, so
// tracking it here covers every round, US and Russian, without touching any
// prefab.
modded class SCR_MortarShellGadgetComponent
{
	protected ref OYB_ShotTracker m_OYB_Tracker;
	protected float m_fOYB_LastTime;

	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		World world = GetGame().GetWorld();
		if (!world)
			return;
		m_OYB_Tracker = new OYB_ShotTracker();
		m_fOYB_LastTime = world.GetWorldTime();
		GetGame().GetCallqueue().CallLater(OYB_Watch, 0, true, owner);
	}

	protected void OYB_Watch(IEntity owner)
	{
		World world = GetGame().GetWorld();
		if (!owner || !world)
			return;
		float now = world.GetWorldTime();
		m_OYB_Tracker.Step(owner.GetOrigin(), (now - m_fOYB_LastTime) / 1000);
		m_fOYB_LastTime = now;
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(OYB_Watch);
		if (m_OYB_Tracker)
			m_OYB_Tracker.Finish();
		super.OnDelete(owner);
	}
}
