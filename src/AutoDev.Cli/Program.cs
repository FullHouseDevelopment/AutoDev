if (args is ["migration-probe"])
{
    Console.Out.WriteLine("migration-scaffold-ready");
    return 0;
}

Console.Error.WriteLine(
    "AutoDev C# migration host scaffold: no product commands have been migrated.");
return 2;
