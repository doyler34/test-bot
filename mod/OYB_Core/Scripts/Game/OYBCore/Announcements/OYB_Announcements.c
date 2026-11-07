// Announcements set on the panel's Announcements tab, shown to each player as a
// hint on their own screen, never in chat. The panel writes
// $profile:OYB_Core/announcements.json:
//   { "title": "OYB", "welcome": "...", "messages": ["...", ...], "everyMinutes": 30 }
// The server reads it when a game starts and sends it to each player as they
// join. The player's game shows the welcome once they're in the world, then one
// message every everyMinutes, in turn. A change on the panel takes effect at the
// next game.
modded class SCR_PlayerController
{
	void OYB_SendAnnouncements(string title, string welcome, notnull array<string> messages, int everyMinutes)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_Announcements(title, welcome, messages, everyMinutes);
		else
			Rpc(OYB_RpcDo_Announcements, title, welcome, messages, everyMinutes);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_Announcements(string title, string welcome, array<string> messages, int everyMinutes)
	{
		OYB_Announcements.Get().Start(title, welcome, messages, everyMinutes);
	}
}

// Ordered so the method added to SCR_PlayerController compiles before
// OYB_Announcements calls it.
class OYB_Announcements
{
	protected static const string FILE = "$profile:OYB_Core/announcements.json";
	protected static const int SHOW_SECONDS = 12;
	protected static const int IN_WORLD_CHECK_MS = 5000;

	protected static ref OYB_Announcements s_Instance;

	protected string m_sTitle = "OYB";
	protected string m_sWelcome;
	protected ref array<string> m_aMessages = {};
	protected int m_iEveryMinutes;
	protected int m_iNext;

	static OYB_Announcements Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_Announcements();

		return s_Instance;
	}

	// On the server, at the start of a game.
	void Load()
	{
		m_sTitle = "OYB";
		m_sWelcome = string.Empty;
		m_aMessages.Clear();
		m_iEveryMinutes = 0;

		bool exists;
		string text;
		if (!OYB_VehicleSettings.ReadFile(FILE, exists, text))
		{
			PrintFormat("OYB_CORE announcements: %1 couldn't be read, none this game", FILE, level: LogLevel.WARNING);
			return;
		}
		if (!exists || text.Trim().IsEmpty())
			return;

		string title;
		JsonLoadContext context = new JsonLoadContext();
		if (!context.LoadFromString(text))
		{
			PrintFormat("OYB_CORE announcements: %1 isn't readable, none this game", FILE, level: LogLevel.WARNING);
			return;
		}
		context.ReadValue("title", title);
		context.ReadValue("welcome", m_sWelcome);
		context.ReadValue("messages", m_aMessages);
		context.ReadValue("everyMinutes", m_iEveryMinutes);
		if (!title.Trim().IsEmpty())
			m_sTitle = title;

		PrintFormat("OYB_CORE announcements: welcome %1, %2 messages every %3 minutes",
			!m_sWelcome.Trim().IsEmpty(), m_aMessages.Count(), m_iEveryMinutes);
	}

	void SendTo(int playerId)
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
		if (controller)
			controller.OYB_SendAnnouncements(m_sTitle, m_sWelcome, m_aMessages, m_iEveryMinutes);
	}

	// On the player's game, when the server sends them.
	void Start(string title, string welcome, notnull array<string> messages, int everyMinutes)
	{
		ScriptCallQueue queue = GetGame().GetCallqueue();
		queue.Remove(ShowWelcome);
		queue.Remove(ShowNext);

		m_sTitle = title;
		m_sWelcome = welcome;
		m_aMessages.Copy(messages);
		m_iEveryMinutes = everyMinutes;
		m_iNext = 0;

		queue.CallLater(ShowWelcome, IN_WORLD_CHECK_MS, true);
	}

	// Waits until the player is in the world (not on the deploy screen).
	protected void ShowWelcome()
	{
		PlayerController controller = GetGame().GetPlayerController();
		if (!controller || !controller.GetControlledEntity())
			return;

		ScriptCallQueue queue = GetGame().GetCallqueue();
		queue.Remove(ShowWelcome);

		if (!m_sWelcome.Trim().IsEmpty())
			SCR_HintManagerComponent.ShowCustomHint(m_sWelcome, m_sTitle, SHOW_SECONDS);

		if (!m_aMessages.IsEmpty() && m_iEveryMinutes > 0)
			queue.CallLater(ShowNext, m_iEveryMinutes * 60000, true);
	}

	protected void ShowNext()
	{
		if (m_aMessages.IsEmpty())
			return;

		if (m_iNext >= m_aMessages.Count())
			m_iNext = 0;

		SCR_HintManagerComponent.ShowCustomHint(m_aMessages[m_iNext], m_sTitle, SHOW_SECONDS);
		m_iNext++;
	}
}

modded class SCR_BaseGameMode
{
	override protected void OnGameStart()
	{
		super.OnGameStart();

		if (Replication.IsServer())
			OYB_Announcements.Get().Load();
	}

	override void OnPlayerConnected(int playerId)
	{
		super.OnPlayerConnected(playerId);

		if (Replication.IsServer())
			GetGame().GetCallqueue().CallLater(OYB_SendAnnouncements, 3000, false, playerId);
	}

	protected void OYB_SendAnnouncements(int playerId)
	{
		OYB_Announcements.Get().SendTo(playerId);
	}
}
