// Copies the game's own mortar data - the configs the ballistic manual and the
// wind tables are built from - out of its packed files into the profile folder,
// so the whole lot can be sent over as plain text. Runs once, the first time a
// mortar round appears. Every file it finds is also named in the log with
// OYB_MANUAL in front, in case a copy fails.
//
// If this file ever stops the mod loading, delete it: the shot tracking doesn't use it.
class OYB_ManualDump
{
	protected static const string TARGET = "$profile:OYB_Manual";
	protected static bool s_bDone;
	protected static ref map<string, bool> s_mFound;
	protected static ref map<string, bool> s_mSearched;

	static void Run()
	{
		if (s_bDone)
			return;
		s_bDone = true;
		s_mFound = new map<string, bool>();
		s_mSearched = new map<string, bool>();
		FileIO.MakeDirectory(TARGET);
		FileIO.FindFiles(Found, "$ArmaReforger:Configs", "");

		int copied = 0;
		foreach (string path, bool unused : s_mFound)
		{
			string flat = path;
			flat.Replace("$ArmaReforger:", "");
			flat.Replace("/", "_");
			flat.Replace("\\", "_");
			bool ok = FileIO.CopyFile(path, TARGET + "/" + flat);
			if (ok)
				copied++;
			PrintFormat("OYB_MANUAL %1 %2", path, ok);
		}
		PrintFormat("OYB_MANUAL done: %1 of %2 files copied to %3", copied, s_mFound.Count(), TARGET);
	}

	protected static void Found(string fileName, FileAttribute attributes = 0, string filesystem = "")
	{
		if (attributes & FileAttribute.DIRECTORY)
		{
			// Searching a folder may or may not include its subfolders; this covers both.
			if (!s_mSearched.Contains(fileName))
			{
				s_mSearched.Insert(fileName, true);
				FileIO.FindFiles(Found, fileName, "");
			}
			return;
		}
		string lower = fileName;
		lower.ToLower();
		if (!lower.Contains(".conf"))
			return;
		if (lower.Contains("ballist") || lower.Contains("winddata") || lower.Contains("mortar")
			|| lower.Contains("m821") || lower.Contains("m819") || lower.Contains("m853")
			|| lower.Contains("832") || lower.Contains("chargering") || lower.Contains("artillery"))
			s_mFound.Insert(fileName, true);
	}
}
