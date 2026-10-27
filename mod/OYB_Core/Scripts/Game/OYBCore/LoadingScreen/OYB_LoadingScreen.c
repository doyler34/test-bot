// OYB's own picture on the scenario loading screen. The game's loading screen
// (ArmaReforgerLoadingAnim, layout UI/layouts/Menus/LoadingScreen/ScenarioLoadingScreen.layout)
// shows the scenario's loading image once the mission header has arrived; this
// puts OYB's image in that same picture slot straight after, so the progress
// bar, spinner, hints, title and the rest stay the game's own.
modded class SCR_LoadingScreenComponent
{
	override void OnMissionDataRetrieved(SCR_MissionHeader header)
	{
		super.OnMissionDataRetrieved(header);

		// Without a header the game keeps its first, scenario-less screen.
		if (header)
			OYB_LoadingScreen.Apply(m_Widgets);
	}
}

class OYB_LoadingScreen
{
	// The imported image's resource name, as Workbench gives it after import:
	// "{GUID}UI/Textures/LoadingScreen/OYB_LoadingScreen.edds". Empty means the
	// game's own image is kept.
	static const ResourceName IMAGE = "";

	static void Apply(SCR_ScenarioLoadingScreenWidgets widgets)
	{
		if (IMAGE.IsEmpty() || !widgets || !widgets.m_wLoadingImage)
			return;

		if (!widgets.m_wLoadingImage.LoadImageTexture(0, IMAGE))
			Print("OYB_CORE couldn't load the loading screen image " + IMAGE, LogLevel.WARNING);
	}
}
