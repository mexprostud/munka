from resources.lib.helper import Helper
from resources.lib.sweettv import refreshToken
import xbmc

if __name__ == '__main__':
    monitor = xbmc.Monitor()
    helper = Helper()

    while not monitor.abortRequested():
        refreshToken()
        if monitor.waitForAbort(30 * 60):
            break
