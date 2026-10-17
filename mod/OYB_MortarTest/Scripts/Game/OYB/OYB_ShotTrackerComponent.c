// Only needed if a round ever turns up without SCR_MortarShellGadgetComponent:
// add this to its prefab by hand and it reports the same way.

[ComponentEditorProps(category: "OYB", description: "Reports where a mortar round was fired from and where it landed")]
class OYB_ShotTrackerComponentClass : ScriptComponentClass
{
}

class OYB_ShotTrackerComponent : ScriptComponent
{
	protected ref OYB_ShotTracker m_Tracker = new OYB_ShotTracker();

	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		SetEventMask(owner, EntityEvent.FRAME);
	}

	override void EOnFrame(IEntity owner, float timeSlice)
	{
		m_Tracker.Step(owner.GetOrigin(), timeSlice);
	}

	override void OnDelete(IEntity owner)
	{
		m_Tracker.Finish();
		super.OnDelete(owner);
	}
}
