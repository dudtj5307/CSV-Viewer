' CSV Viewer - install confirmation (InstallUISequence, runs before ExecuteAction)
' Function target so the return value reaches Windows Installer:
'   1 (msiDoActionStatusSuccess)  -> proceed with install
'   2 (msiDoActionStatusUserExit) -> clean cancel (no fatal-error dialog)
Function ConfirmInstall()
    Dim answer
    answer = MsgBox("Do you wish to install CSV Viewer?", _
                    vbYesNo + vbQuestion + vbSystemModal, "CSV Viewer")
    If answer = vbNo Then
        ConfirmInstall = 2
    Else
        ConfirmInstall = 1
    End If
End Function
