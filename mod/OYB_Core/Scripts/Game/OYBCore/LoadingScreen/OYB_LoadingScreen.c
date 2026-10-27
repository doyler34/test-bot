// OYB's own picture across the whole scenario loading screen. The game's loading
// screen (ArmaReforgerLoadingAnim, layout UI/layouts/Menus/LoadingScreen/ScenarioLoadingScreen.layout)
// keeps its progress bar, spinner, hints and warnings; OYB's image goes behind
// them in place of the background, and the scenario's photo, title and
// description are hidden, as the image carries OYB's own branding.
modded class SCR_LoadingScreenComponent
{
	override protected void InitWidgets()
	{
		super.InitWidgets();

		OYB_LoadingScreen.AddBackground(m_Widgets);
	}

	override void OnMissionDataRetrieved(SCR_MissionHeader header)
	{
		super.OnMissionDataRetrieved(header);

		// Without a header the game keeps its first, scenario-less screen.
		if (header)
			OYB_LoadingScreen.HideScenarioDetails(m_Widgets);
	}
}

class OYB_LoadingScreen
{
	// The imported image (UI/Textures/LoadingScreen/OYB_LoadingScreen.edds, GUID
	// from its .meta). Empty keeps the game's own loading screen.
	static const ResourceName IMAGE = "{77B814A1D59FDB3A}UI/Textures/LoadingScreen/OYB_LoadingScreen.edds";

	// The image fills the screen at its own shape: as large as it needs to be to
	// cover the screen, centred, so a screen of another shape crops the edges
	// rather than stretching it.
	static void AddBackground(SCR_ScenarioLoadingScreenWidgets widgets)
	{
		if (IMAGE.IsEmpty() || !widgets || !widgets.m_wBackground)
			return;

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		ImageWidget image = ImageWidget.Cast(workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.STRETCH | WidgetFlags.BLEND, Color.FromInt(Color.WHITE), 0, widgets.m_wBackground));
		if (!image)
			return;

		if (!image.LoadImageTexture(0, IMAGE))
		{
			Print("OYB_CORE couldn't load the loading screen image " + IMAGE, LogLevel.WARNING);
			image.RemoveFromHierarchy();
			return;
		}

		int imageWidth, imageHeight;
		image.GetImageSize(0, imageWidth, imageHeight);
		float screenWidth = workspace.DPIUnscale(workspace.GetWidth());
		float screenHeight = workspace.DPIUnscale(workspace.GetHeight());
		if (imageWidth > 0 && imageHeight > 0 && screenWidth > 0 && screenHeight > 0)
		{
			float imageAspect = imageWidth;
			imageAspect /= imageHeight;
			float width = screenWidth;
			float height = screenWidth / imageAspect;
			if (height < screenHeight)
			{
				height = screenHeight;
				width = screenHeight * imageAspect;
			}
			image.SetSize(width, height);
		}

		AlignableSlot.SetHorizontalAlign(image, LayoutHorizontalAlign.Center);
		AlignableSlot.SetVerticalAlign(image, LayoutVerticalAlign.Center);
	}

	static void HideScenarioDetails(SCR_ScenarioLoadingScreenWidgets widgets)
	{
		if (IMAGE.IsEmpty() || !widgets)
			return;

		if (widgets.m_wPhotoGroup)
			widgets.m_wPhotoGroup.SetVisible(false);
		if (widgets.m_wTextGroup)
			widgets.m_wTextGroup.SetVisible(false);
		if (widgets.m_wReforgerLogo)
			widgets.m_wReforgerLogo.SetVisible(false);
	}
}
